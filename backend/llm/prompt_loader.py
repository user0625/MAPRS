from __future__ import annotations

from pathlib import Path
from string import Formatter
from typing import Any


class PromptTemplateError(Exception):
  """Raised when prompt template loading or rendering fails."""


class PromptTemplateLoader:
  """
    Load and render Markdown prompt templates.

    Templates are stored under backend/prompts by default.
  """

  def __init__(self, prompt_dir: str | Path | None = None) -> None:
    if prompt_dir is None:
      # backend/llm/prompt_loader.py -> backend/
      backend_dir = Path(__file__).resolve().parents[1]
      prompt_dir = backend_dir / "prompts"

    self.prompt_dir = Path(prompt_dir)

  def load(self, template_name: str) -> str:
    """
      Load a prompt template by filename.
    """

    if not template_name.strip():
      raise PromptTemplateError("template_name cannot be empty.")

    template_path = self.prompt_dir / template_name

    if not template_path.exists():
      raise PromptTemplateError(f"Prompt template does not exist: {template_path}")

    if not template_path.is_file():
      raise PromptTemplateError(f"Prompt template is not a file: {template_path}")

    try:
      return template_path.read_text(encoding="utf-8")
    except Exception as exc:
      raise PromptTemplateError(f"Failed to read prompt template: {template_path}") from exc

  def render(self, template_name: str, **kwargs: Any) -> str:
    """
      Render a prompt template with keyword arguments.
    """

    template = self.load(template_name)
    required_fields = self.get_template_variables(template)

    missing_fields = sorted(required_fields - set(kwargs.keys()))
    if missing_fields:
      raise PromptTemplateError(
        f"Missing template variables for {template_name}: {missing_fields}"
      )

    safe_kwargs = {
      key: self._to_prompt_text(value)
      for key, value in kwargs.items()
    }

    try:
      return template.format(**safe_kwargs).strip()
    except Exception as exc:
      raise PromptTemplateError(f"Failed to render prompt template: {template_name}") from exc

  def get_template_variables(self, template: str) -> set[str]:
    """
      Return variable names used in a format-style template.
    """

    variables: set[str] = set()

    for _, field_name, _, _ in Formatter().parse(template):
      if field_name:
        # Handles simple fields like {title}.
        # Does not support nested expressions.
        variables.add(field_name.split(".")[0].split("[")[0])

    return variables

  def _to_prompt_text(self, value: Any) -> str:
    """
      Convert Python values into prompt-friendly text.
    """

    if value is None:
      return "Unknown"

    if isinstance(value, str):
      return value

    if isinstance(value, list):
      if not value:
        return "None provided."
      return "\n".join(f"- {item}" for item in value)

    if isinstance(value, tuple):
      if not value:
        return "None provided."
      return "\n".join(f"- {item}" for item in value)

    if isinstance(value, dict):
      if not value:
        return "{}"
      return "\n".join(f"- {key}: {val}" for key, val in value.items())

    return str(value)