from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TypeVar
from pydantic import BaseModel

from backend.llm.client import BaseLLMClient, LLMError

class AgentError(Exception):
  """Raised when an agent fails."""

InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)



class BaseAgent(ABC):
  """
    Base class for all agents.

    Each concrete agent should:
    - receive a Pydantic input schema;
    - return a Pydantic output schema;
    - use LLMClient instead of calling model APIs directly.
  """

  def __init__(self, name: str, llm_client: BaseLLMClient, ) -> None:
    if not name.strip():
      raise ValueError("agent name cannot be empty.")

    self.name = name.strip()
    self.llm_client = llm_client

  @abstractmethod
  def run(self, agent_input: InputT) -> OutputT:
    """
      Run the agent.
      Concrete agents must implement this method.
    """

  @property
  def is_mock(self) -> bool:
    """Return whether this agent is using mock LLM."""
    return self.llm_client.provider == "mock"

  def generate_pydantic(
    self,
    prompt: str,
    output_schema: type[OutputT],
    system_prompt: str | None = None,
    temperature: float = 0.1,
    max_tokens: int | None = None,
  ) -> OutputT:
    """
      Generate a Pydantic output through LLMClient.
    """
    try:
      return self.llm_client.generate_pydantic(
        prompt=prompt,
        output_schema=output_schema,
        system_prompt=system_prompt,
        temperature=temperature,
        max_tokens=max_tokens,
      )
    except LLMError as exc:
      raise AgentError(f"{self.name} failed to generate valid output.") from exc

  def build_schema_instruction(self, output_schema: type[BaseModel]) -> str:
    """
      Build a simple schema instruction for JSON output.

      This does not rely on vendor-specific structured output.
    """
    schema = output_schema.model_json_schema()
    return (
      "You must return a valid JSON object that matches the following schema. "
      "Do not include Markdown code fences. Do not include explanations outside JSON.\n\n"
      f"{schema}"
    )