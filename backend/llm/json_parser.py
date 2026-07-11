from __future__ import annotations

import json
import re
from typing import Any


class JSONParseError(Exception):
  """Raised when JSON extraction or parsing fails."""


def parse_json_object(text: str) -> dict[str, Any]:
  """
    Parse a JSON object from LLM output.

    Supports:
    - pure JSON
    - fenced ```json blocks
    - explanatory text before/after JSON
    - simple trailing commas
  """

  if not text or not text.strip():
    raise JSONParseError("Cannot parse empty text as JSON.")

  candidates = _build_candidates(text)
  last_error: Exception | None = None

  for candidate in candidates:
    candidate = _remove_trailing_commas(candidate.strip())

    try:
      parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
      last_error = exc
      continue

    if not isinstance(parsed, dict):
      raise JSONParseError("Expected a JSON object, but got non-object JSON.")

    return parsed

  raise JSONParseError(f"Failed to parse JSON object from text: {text}") from last_error


def _build_candidates(text: str) -> list[str]:
  candidates: list[str] = []

  stripped = text.strip()
  candidates.append(stripped)

  fenced = _extract_fenced_json(stripped)
  if fenced is not None:
    candidates.append(fenced)

  balanced = _extract_first_balanced_json_object(stripped)
  if balanced is not None:
    candidates.append(balanced)

  unique_candidates: list[str] = []
  seen: set[str] = set()

  for candidate in candidates:
      if candidate not in seen:
        unique_candidates.append(candidate)
        seen.add(candidate)

  return unique_candidates


def _extract_fenced_json(text: str) -> str | None:
  pattern = r"```(?:json|JSON)?\s*(.*?)```"
  match = re.search(pattern, text, flags=re.DOTALL)

  if not match:
    return None

  return match.group(1).strip()


def _extract_first_balanced_json_object(text: str) -> str | None:
  start = text.find("{")
  if start == -1:
    return None

  depth = 0
  in_string = False
  escape = False

  for index in range(start, len(text)):
    char = text[index]

    if escape:
      escape = False
      continue

    if char == "\\":
      escape = True
      continue

    if char == '"':
      in_string = not in_string
      continue

    if in_string:
      continue

    if char == "{":
      depth += 1
    elif char == "}":
      depth -= 1

      if depth == 0:
          return text[start : index + 1]

  return None


def _remove_trailing_commas(text: str) -> str:
  return re.sub(r",\s*([}\]])", r"\1", text)