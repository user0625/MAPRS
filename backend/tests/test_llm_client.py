import pytest
from pydantic import BaseModel

from backend.llm.client import (
    LLMError,
    LLMMessage,
    MockLLMClient,
)


class DummyOutput(BaseModel):
    mock: bool
    prompt_preview: str


def test_llm_message_rejects_empty_content():
    with pytest.raises(ValueError):
        LLMMessage(role="user", content="   ")


def test_mock_llm_generate():
    client = MockLLMClient()

    response = client.generate(
        messages=[
            LLMMessage(
                role="user",
                content="Analyze this paper.",
            )
        ]
    )

    assert response.provider == "mock"
    assert response.model == "mock-llm"
    assert "[MOCK RESPONSE]" in response.content


def test_mock_llm_generate_from_prompt():
    client = MockLLMClient()

    response = client.generate_from_prompt(
        prompt="Summarize the method.",
        system_prompt="You are a paper reading assistant.",
    )

    assert response.provider == "mock"
    assert "Summarize the method" in response.content


def test_mock_llm_generate_json():
    client = MockLLMClient()

    data = client.generate_json(
        prompt="Return JSON.",
    )

    assert data["mock"] is True
    assert "prompt_preview" in data


def test_generate_pydantic_with_mock_client():
    client = MockLLMClient()

    output = client.generate_pydantic(
        prompt="Return dummy JSON.",
        output_schema=DummyOutput,
    )

    assert output.mock is True


def test_parse_json_response_with_code_fence():
    client = MockLLMClient()

    parsed = client._parse_json_response(
        """```json
{"name": "test", "value": 1}
```"""
    )

    assert parsed["name"] == "test"
    assert parsed["value"] == 1


def test_parse_json_response_rejects_invalid_json():
    client = MockLLMClient()

    with pytest.raises(LLMError):
        client._parse_json_response("not json")