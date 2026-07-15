from __future__ import annotations

import logging
import hashlib

from abc import ABC, abstractmethod

from typing import Any, Iterator, Literal, TypeVar

from pydantic import BaseModel, Field, field_validator, ValidationError

from backend.core.config import AppSettings
from backend.core.request_policy import RequestPolicy, RequestPolicyError
from backend.llm.json_parser import JSONParseError, parse_json_object

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """Raised when LLM call or parsing fails."""


class LLMMessage(BaseModel):
    """
    A chat message sent to the LLM.
    """

    role: Literal["system", "user", "assistant"] = "user"
    content: str = Field(..., min_length=1)

    @field_validator("content")
    @classmethod
    def strip_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message content cannot be empty.")

        return value


class LLMResponse(BaseModel):
    """
    Unified response returned by all LLM clients.
    """

    content: str
    model: str
    provider: str
    raw_response: Any | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("content", "model", "provider")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Field cannot be empty.")
        return value


T = TypeVar("T", bound=BaseModel)


class BaseLLMClient(ABC):
    """
    Base interface for all LLM clients.
    """

    model_name: str
    provider: str
    structured_output_stats: dict[str, Any]

    def _ensure_usage_stats(self) -> dict[str, int]:
        if not hasattr(self, "usage_stats"):
            self.usage_stats = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "calls": 0}
        return self.usage_stats

    def _record_response_usage(self, response: LLMResponse) -> None:
        stats = self._ensure_usage_stats()
        usage = response.metadata.get("usage") or {}
        input_tokens = int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0)
        output_tokens = int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0)
        stats["input_tokens"] += input_tokens
        stats["output_tokens"] += output_tokens
        stats["total_tokens"] += int(usage.get("total_tokens", input_tokens + output_tokens) or 0)
        stats["calls"] += 1

    def _ensure_stats(self) -> dict[str, Any]:
        if not hasattr(self, "structured_output_stats"):
            self.structured_output_stats = {
                "total_calls": 0,
                "first_attempt_successes": 0,
                "retried_calls": 0,
                "final_failures": 0,
                "by_schema": {},
            }
        return self.structured_output_stats

    @abstractmethod
    def generate(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """
        Generate a response from messages.
        """

    def stream(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> Iterator[str]:
        """Stream text chunks. Clients without native streaming remain compatible."""
        yield self.generate(messages, temperature, max_tokens).content

    def generate_from_prompt(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """
        Convenience method for single-turn prompt generation.
        """
        messages: list[LLMMessage] = []
        if system_prompt:
            messages.append(
                LLMMessage(
                    role="system",
                    content=system_prompt,
                )
            )

        messages.append(
            LLMMessage(
                role="user",
                content=prompt,
            )
        )

        return self.generate(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def generate_json(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.1,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """
        Generate JSON and parse it into a Python dictionary.

        This is useful for Agent outputs before adding full structured-output support.
        """
        response = self.generate_from_prompt(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        self._record_response_usage(response)
        return self._parse_json_response(response.content)

    def generate_pydantic(
        self,
        prompt: str,
        output_schema: type[T],
        system_prompt: str | None = None,
        temperature: float = 0.1,
        max_tokens: int | None = None,
        max_retries: int = 2,
    ) -> T:
        """
        Generate JSON and validate it against a Pydantic schema.

        if parsing or validation fails, retry with error feedback.
        """

        stats = self._ensure_stats()
        stats["total_calls"] += 1
        schema_stats = stats["by_schema"].setdefault(
            output_schema.__name__,
            {"calls": 0, "attempts": 0, "successes": 0, "failures": 0},
        )
        schema_stats["calls"] += 1
        current_prompt = prompt
        last_error: Exception | None = None

        for attempt in range(max_retries + 1):
            schema_stats["attempts"] += 1
            try:
                data = self.generate_json(
                    prompt=current_prompt,
                    system_prompt=system_prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )

                result = output_schema.model_validate(data)
                schema_stats["successes"] += 1
                if attempt == 0:
                    stats["first_attempt_successes"] += 1
                else:
                    stats["retried_calls"] += 1
                return result
            except (LLMError, ValidationError, ValueError) as exc:
                last_error = exc
                if attempt >= max_retries:
                    stats["final_failures"] += 1
                    schema_stats["failures"] += 1
                    error_kind = self._classify_structured_output_error(exc)
                    raise LLMError(
                        f"{error_kind} for {output_schema.__name__} after "
                        f"{max_retries + 1} attempts. Last error: {exc}"
                    ) from exc
                current_prompt = self._build_retry_prompt(
                    original_prompt=prompt,
                    output_schema=output_schema,
                    error_message=str(exc),
                )
        raise LLMError(
            f"Failed to generate valid {output_schema.__name__}"
        ) from last_error

    @staticmethod
    def _classify_structured_output_error(exc: Exception) -> str:
        if isinstance(exc, ValidationError):
            return "Schema validation failed"
        if isinstance(exc, LLMError):
            if str(exc).startswith("Failed to call"):
                return "LLM API call failed"
            if str(exc).startswith("Failed to parse"):
                return "JSON parsing failed"
        return "Structured output generation failed"

    def _build_retry_prompt(
        self,
        original_prompt: str,
        output_schema: type[BaseModel],
        error_message: str,
    ) -> str:
        schema = output_schema.model_json_schema()

        return f"""
            The previous response failed JSON parsing or schema validation.

            Validation error:
            {error_message}

            Please try again.

            Important requirements:
            1. Return only one valid JSON object.
            2. Do not include Markdown code fences.
            3. Do not include explanations outside JSON.
            4. Do not omit required fields.
            5. Do not use null unless the schema allows it.
            6. The JSON object must match this schema:

            {schema}

            Original task:
            {original_prompt}
            """.strip()

    def _parse_json_response(self, content: str) -> dict[str, Any]:
        try:
            return parse_json_object(content)
        except JSONParseError as exc:
            logger.error(
                "Failed to parse LLM JSON response",
                extra={
                    "error_code": "json_parse",
                    "response_length": len(content),
                    "response_sha256": hashlib.sha256(content.encode()).hexdigest()[
                        :12
                    ],
                },
            )
            raise LLMError("Failed to parse LLM response as JSON.") from exc

    # def _parse_json_response(self, content: str) -> dict[str, Any]:
    #   """
    #     Parse JSON response.

    #     This method handles simple cases where the model returns:
    #     - pure JSON
    #     - fenced ```json blocks
    #   """

    #   cleaned = content.strip()

    #   if cleaned.startswith("```"):
    #     cleaned = self._strip_code_fence(cleaned)

    #   try:
    #     parsed = json.loads(cleaned)
    #   except json.JSONDecodeError as exc:
    #     raise LLMError(f"Failed to parse LLM response as JSON: {content}") from exc
    #   if not isinstance(parsed, dict):
    #     raise LLMError("Expected JSON object from LLM response.")

    #   return parsed

    def _strip_code_fence(self, content: str) -> str:
        """
        Remove Markdown code fences from model output.
        """
        lines = content.strip().splitlines()

        if lines and lines[0].startswith("```"):
            lines = lines[1:]

        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]

        return "\n".join(lines).strip()


class MockLLMClient(BaseLLMClient):
    """
    Mock LLM client for local development and unit tests.

    It returns deterministic responses and does not call any external API.
    """

    def __init__(
        self,
        model_name: str = "mock-llm",
        provider: str = "mock",
    ) -> None:
        self.model_name = model_name
        self.provider = provider

    def generate(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        if not messages:
            raise LLMError("messages cannot be empty.")

        last_message = messages[-1].content

        return LLMResponse(
            content=f"[MOCK RESPONSE] {last_message[:200]}",
            model=self.model_name,
            provider=self.provider,
            metadata={
                "temperature": temperature,
                "max_tokens": max_tokens,
                "usage": {
                    "input_tokens": len(last_message.split()),
                    "output_tokens": min(200, len(last_message.split()) + 2),
                    "total_tokens": len(last_message.split()) + min(200, len(last_message.split()) + 2),
                },
            },
        )

    def generate_json(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.1,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """
        Return a minimal mock JSON object.

        Agent-specific mock outputs can be implemented later in each Agent.
        """
        return {
            "mock": True,
            "prompt_preview": prompt[:200],
        }

    def stream(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> Iterator[str]:
        content = self.generate(messages, temperature, max_tokens).content
        for start in range(0, len(content), 24):
            yield content[start : start + 24]


class OpenAICompatibleLLMClient(BaseLLMClient):
    """
    LLM client for OpenAI-compatible chat APIs.

    Can be used with:
    - OpenAI
    - Qwen compatible API
    - DeepSeek compatible API
    - other OpenAI-compatible services
    """

    def __init__(
        self,
        api_key: str,
        model_name: str,
        base_url: str | None = None,
        provider: str = "openai_compatible",
    ) -> None:
        if not api_key.strip():
            raise ValueError("api_key cannot be empty.")

        if not model_name.strip():
            raise ValueError("model_name cannot be empty.")

        try:
            from openai import OpenAI
        except ImportError as exc:
            raise LLMError(
                "openai package is required for OpenAICompatibleLLMClient. "
                "Install it with: uv add openai"
            ) from exc

        self.model_name = model_name
        self.provider = provider
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            max_retries=0,
        )
        self.request_policy: RequestPolicy | None = None

    def generate(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        if not messages:
            raise LLMError("messages cannot be empty.")

        payload_messages = [
            {
                "role": message.role,
                "content": message.content,
            }
            for message in messages
        ]

        kwargs: dict[str, Any] = {
            "model": self.model_name,
            "messages": payload_messages,
            "temperature": temperature,
        }

        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        try:

            def operation():
                return self.client.chat.completions.create(**kwargs)

            response = (
                self.request_policy.call(operation)
                if self.request_policy
                else operation()
            )
        except RequestPolicyError as exc:
            raise LLMError(str(exc)) from exc
        except Exception as exc:
            raise LLMError("Failed to call OpenAI-compatible chat API.") from exc

        content = response.choices[0].message.content

        if content is None:
            raise LLMError("LLM returned empty content.")

        usage = getattr(response, "usage", None)
        return LLMResponse(
            content=content,
            model=self.model_name,
            provider=self.provider,
            raw_response=response,
            metadata={
                "temperature": temperature,
                "max_tokens": max_tokens,
                "usage": {
                    "input_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                    "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
                    "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
                },
            },
        )

    def stream(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> Iterator[str]:
        kwargs: dict[str, Any] = {
            "model": self.model_name,
            "messages": [message.model_dump() for message in messages],
            "temperature": temperature,
            "stream": True,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        try:
            response = self.client.chat.completions.create(**kwargs)
            for part in response:
                token = part.choices[0].delta.content
                if token:
                    yield token
        except Exception as exc:
            raise LLMError("Failed to stream OpenAI-compatible chat API.") from exc


def create_llm_client(settings: AppSettings) -> BaseLLMClient:
    """
    Create LLM client from AppSettings.
    """

    if settings.llm_provider == "mock":
        return MockLLMClient(
            model_name=settings.llm_model,
            provider="mock",
        )

    if settings.llm_provider == "openai_compatible":
        if not settings.llm_api_key:
            raise ValueError("llm_api_key is required for openai_compatible provider.")
        client = OpenAICompatibleLLMClient(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model_name=settings.llm_model,
            provider=settings.llm_vendor,
        )
        client.client = client.client.with_options(
            timeout=(settings.request_connect_timeout, settings.request_read_timeout),
            max_retries=0,
        )
        client.request_policy = RequestPolicy.from_settings(settings)
        return client
    raise ValueError(f"Unsupported llm_provider: {settings.llm_provider}")
