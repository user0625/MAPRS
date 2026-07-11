import pytest
from pydantic import BaseModel

from backend.llm.client import BaseLLMClient, LLMError, LLMMessage, LLMResponse


class ExpectedOutput(BaseModel):
    value: str


class RetryLLMClient(BaseLLMClient):
    def __init__(self) -> None:
        self.model_name = "retry-mock"
        self.provider = "mock"
        self.calls = 0

    def generate(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        self.calls += 1

        if self.calls == 1:
            content = '{"wrong_field": "bad"}'
        else:
            content = '{"value": "ok"}'

        return LLMResponse(
            content=content,
            model=self.model_name,
            provider=self.provider,
        )


class AlwaysInvalidLLMClient(BaseLLMClient):
    def __init__(self) -> None:
        self.model_name = "invalid-mock"
        self.provider = "mock"
        self.calls = 0

    def generate(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        self.calls += 1

        return LLMResponse(
            content='{"wrong_field": "bad"}',
            model=self.model_name,
            provider=self.provider,
        )


def test_generate_pydantic_retries_after_validation_error():
    client = RetryLLMClient()

    output = client.generate_pydantic(
        prompt="Return JSON.",
        output_schema=ExpectedOutput,
        max_retries=1,
    )

    assert output.value == "ok"
    assert client.calls == 2


def test_generate_pydantic_fails_after_retries():
    client = AlwaysInvalidLLMClient()

    with pytest.raises(LLMError) as exc_info:
        client.generate_pydantic(
            prompt="Return JSON.",
            output_schema=ExpectedOutput,
            max_retries=2,
        )

    assert "Schema validation failed for ExpectedOutput after 3 attempts" in str(exc_info.value)
    assert "wrong_field" in str(exc_info.value)
    assert client.calls == 3
