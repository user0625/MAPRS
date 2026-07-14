from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from backend.api.task_store import APITaskStatus


def completed_task(store: Any, task_id: str, *, conflict_detail: str) -> Any:
    """Return a completed task while preserving each caller's public error text."""
    task = store.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    if task.status != APITaskStatus.COMPLETED:
        raise HTTPException(status_code=409, detail=conflict_detail)
    return task


def load_task_state(
    task: Any,
    *,
    unavailable_status: int,
    unavailable_detail: str,
    invalid_detail: str,
) -> dict[str, Any]:
    path = Path(task.state_json_path or "")
    if not task.state_json_path or not path.is_file():
        raise HTTPException(status_code=unavailable_status, detail=unavailable_detail)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=invalid_detail) from exc
    if not isinstance(state, dict) or not isinstance(state.get("document"), dict):
        raise HTTPException(status_code=500, detail=invalid_detail)
    return state


def sections_from_state(state: dict[str, Any]) -> list[str]:
    document = state.get("document") or {}
    names = [
        section.get("name")
        for section in document.get("sections", [])
        if isinstance(section, dict)
    ]
    names += [
        chunk.get("section")
        for chunk in document.get("chunks", [])
        if isinstance(chunk, dict)
    ]
    return list(dict.fromkeys(str(name) for name in names if name))


def paper_page_count(task: Any, state: dict[str, Any]) -> int:
    document = state.get("document") or {}
    configured = task.task_metadata.get("num_pages", 0)
    page_count = configured if isinstance(configured, int) else 0
    pages = document.get("pages") or []
    if isinstance(pages, list):
        page_count = max(page_count, len(pages))
    chunk_ends = [
        value
        for chunk in document.get("chunks", [])
        if isinstance(chunk, dict)
        for value in (chunk.get("page_end"), chunk.get("page_start"))
        if isinstance(value, int)
    ]
    return max([page_count, *chunk_ends])


def validate_scope(
    task: Any,
    state: dict[str, Any],
    section: str | None,
    page_start: int | None,
    page_end: int | None,
) -> None:
    if section and section not in sections_from_state(state):
        raise HTTPException(status_code=422, detail="Unknown paper section.")
    if page_start is None or page_end is None:
        return
    page_count = paper_page_count(task, state)
    if page_count and page_end > page_count:
        raise HTTPException(
            status_code=422,
            detail=f"Page range exceeds the paper's {page_count} pages.",
        )
