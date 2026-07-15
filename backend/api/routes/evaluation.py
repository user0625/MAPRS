from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from backend.core.config import get_settings


router = APIRouter(prefix="/api/evaluation", tags=["evaluation"])
SUPPORTED_SCHEMAS = ("public-paper-benchmark-v2", "public-paper-benchmark-v1")


def _report_files(directory: Path) -> list[Path]:
    return sorted(
        (path for path in directory.glob("*.json") if path.is_file()),
        key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True,
    )


def load_report(directory: Path, schema: str | tuple[str, ...]) -> dict[str, Any]:
    schemas = (schema,) if isinstance(schema, str) else schema
    for path in _report_files(directory):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if report.get("schema_version") in schemas:
            # Questions and paragraphs are not part of the public schema. Fail
            # closed if a hand-authored artifact attempts to include them.
            def keys(value: Any) -> set[str]:
                if isinstance(value, dict):
                    return {str(key).casefold() for key in value} | {
                        nested for child in value.values() for nested in keys(child)
                    }
                if isinstance(value, list):
                    return {nested for child in value for nested in keys(child)}
                return set()
            serialized_keys = keys(report)
            if serialized_keys & {
                "question", "questions", "text", "full_text", "paragraphs",
                "api_key", "authorization", "endpoint", "base_url",
            }:
                continue
            return report
    raise FileNotFoundError("No compatible public benchmark report is available.")


def load_public_report(directory: Path) -> dict[str, Any]:
    return load_report(directory, SUPPORTED_SCHEMAS)


@router.get("/report")
def evaluation_report() -> dict[str, Any]:
    settings = get_settings()
    try:
        return load_public_report(settings.resolve_path(settings.evaluation_report_dir))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
