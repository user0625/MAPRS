from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ReportConfiguration(BaseModel):
  analysis_depth: Literal["quick", "standard", "deep"] = "standard"
  target_audience: Literal["general", "researcher", "reviewer"] = "researcher"
  report_template: Literal["standard", "review", "reproducibility"] = "standard"
  custom_sections: list[str] = Field(default_factory=list, max_length=20)

  @field_validator("custom_sections")
  @classmethod
  def validate_sections(cls, values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
      value = value.strip()
      if not value or len(value) > 80:
        raise ValueError("Each custom section must contain 1-80 characters.")
      if value not in result:
        result.append(value)
    return result

  @classmethod
  def from_form(cls, analysis_depth: str, target_audience: str,
                report_template: str, custom_sections: str | None) -> "ReportConfiguration":
    try:
      parsed = json.loads(custom_sections) if custom_sections else []
    except json.JSONDecodeError as exc:
      raise ValueError("custom_sections must be a JSON string array.") from exc
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
      raise ValueError("custom_sections must be a JSON string array.")
    return cls(analysis_depth=analysis_depth, target_audience=target_audience,
               report_template=report_template, custom_sections=parsed)
