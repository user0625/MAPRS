from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TypeVar
from pydantic import BaseModel

from backend.llm.client import BaseLLMClient, LLMError
from backend.llm.prompt_loader import PromptTemplateLoader

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

  def __init__(self, name: str, llm_client: BaseLLMClient, prompt_loader: PromptTemplateLoader|None = None ) -> None:
    if not name.strip():
      raise ValueError("agent name cannot be empty.")

    self.name = name.strip()
    self.llm_client = llm_client
    self.prompt_loader = prompt_loader or PromptTemplateLoader()

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
    max_retries: int = 2,
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
        max_retries=max_retries,
      )
    except LLMError as exc:
      raise AgentError(f"{self.name} failed: {exc}") from exc

  def build_schema_instruction(self, output_schema: type[BaseModel]) -> str:
    """
      Build a simple schema instruction for JSON output.

      This does not rely on vendor-specific structured output.
    """
    schema = output_schema.model_json_schema()
    return (
      "You must return only one valid JSON object.\n"
      "Do not include Markdown code fences.\n"
      "Do not include explanations before or after the JSON.\n"
      "Do not use null unless the schema allows it.\n"
      "Do not omit required fields.\n"
      "The JSON object must match the following schema:\n\n"
      f"{schema}"
    )
