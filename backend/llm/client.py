from __future__ import annotations

import json

from abc import ABC, abstractmethod

from typing import Any, Literal, TypeVar

from pydantic import BaseModel, Field, field_validator

from backend.core.config import AppSettings

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

  @abstractmethod
  def generate( self, messages: list[LLMMessage], temperature: float = 0.2, max_tokens: int | None = None,) -> LLMResponse:
    """
    Generate a response from messages.
    """

  def generate_from_prompt(self, prompt: str, system_prompt: str | None = None, temperature: float = 0.2, max_tokens: int | None = None,) -> LLMResponse:
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

  def generate_json(self, prompt: str, system_prompt: str | None = None, temperature: float = 0.1, max_tokens: int | None = None,) -> dict[str, Any]:
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
    return self._parse_json_response(response.content)

  def generate_pydantic(self, prompt: str, output_schema: type[T], system_prompt: str | None = None, temperature: float = 0.1, max_tokens: int | None = None,) -> T:
    """
      Generate JSON and validate it against a Pydantic schema.
    """
    data = self.generate_json(
      prompt=prompt,
      system_prompt=system_prompt,
      temperature=temperature,
      max_tokens=max_tokens,
    )

    try:
      return output_schema.model_validate(data)
    except Exception as exc:
      raise LLMError(
          f"Failed to validate LLM output as {output_schema.__name__}."
      ) from exc

  def _parse_json_response(self, content: str) -> dict[str, Any]:
    """
      Parse JSON response.

      This method handles simple cases where the model returns:
      - pure JSON
      - fenced ```json blocks
    """

    cleaned = content.strip()

    if cleaned.startswith("```"):
      cleaned = self._strip_code_fence(cleaned)

    try:
      parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
      raise LLMError(f"Failed to parse LLM response as JSON: {content}") from exc
    if not isinstance(parsed, dict):
      raise LLMError("Expected JSON object from LLM response.")

    return parsed

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

  def generate( self, messages: list[LLMMessage], temperature: float = 0.2, max_tokens: int | None = None,) -> LLMResponse:
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
        },
    )

  def generate_json(self, prompt: str, system_prompt: str | None = None, temperature: float = 0.1, max_tokens: int | None = None,) -> dict[str, Any]:
    """
      Return a minimal mock JSON object.

      Agent-specific mock outputs can be implemented later in each Agent.
    """
    return { "mock": True, "prompt_preview": prompt[:200],}
  



class OpenAICompatibleLLMClient(BaseLLMClient):
  """
    LLM client for OpenAI-compatible chat APIs.

    Can be used with:
    - OpenAI
    - Qwen compatible API
    - DeepSeek compatible API
    - other OpenAI-compatible services
  """

  def __init__(self, api_key: str, model_name: str, base_url: str | None = None, provider: str = "openai_compatible",) -> None:
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
    )

  def generate(self, messages: list[LLMMessage], temperature: float = 0.2, max_tokens: int | None = None,) -> LLMResponse:
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
      response = self.client.chat.completions.create(**kwargs)
    except Exception as exc:
      raise LLMError("Failed to call OpenAI-compatible chat API.") from exc

    content = response.choices[0].message.content

    if content is None:
      raise LLMError("LLM returned empty content.")

    return LLMResponse(
      content=content,
      model=self.model_name,
      provider=self.provider,
      raw_response=response,
      metadata={
        "temperature": temperature,
        "max_tokens": max_tokens,
      },
    )



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
    return OpenAICompatibleLLMClient(
      api_key=settings.llm_api_key,
      base_url=settings.llm_base_url,
      model_name=settings.llm_model,
      provider=settings.llm_vendor,
    )
  raise ValueError(f"Unsupported llm_provider: {settings.llm_provider}")