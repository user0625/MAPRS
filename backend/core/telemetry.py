from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


TRACE_SCHEMA_VERSION = "analysis-trace-v1"


class TraceEvent(BaseModel):
    """Content-free workflow telemetry safe to persist with task state."""

    model_config = ConfigDict(extra="forbid")

    stage: str
    status: Literal["success", "failed", "canceled", "partial"]
    started_at: str
    duration_ms: float = Field(ge=0)
    model: str | None = None
    prompt_version: str | None = None
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    retries: int = Field(default=0, ge=0)
    fallback_count: int = Field(default=0, ge=0)
    estimated_cost_usd: float = Field(default=0.0, ge=0)
    evidence_count: int = Field(default=0, ge=0)
    branch_id: str | None = None
    error_class: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def llm_snapshot(client: Any) -> dict[str, int]:
    usage = getattr(client, "usage_stats", {}) or {}
    structured = getattr(client, "structured_output_stats", {}) or {}
    return {
        "input_tokens": int(usage.get("input_tokens", 0)),
        "output_tokens": int(usage.get("output_tokens", 0)),
        "retries": int(structured.get("retried_calls", 0)),
        "failures": int(structured.get("final_failures", 0)),
    }


def usage_delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    return {key: max(0, after.get(key, 0) - before.get(key, 0)) for key in before}


def estimate_cost(
    input_tokens: int, output_tokens: int,
    input_usd_per_million: float, output_usd_per_million: float,
) -> float:
    return round(
        input_tokens * input_usd_per_million / 1_000_000
        + output_tokens * output_usd_per_million / 1_000_000,
        8,
    )


def trace_payload(task_id: str, events: list[TraceEvent]) -> dict[str, Any]:
    totals = {
        "duration_ms": round(sum(item.duration_ms for item in events if item.branch_id is None), 3),
        "input_tokens": sum(item.input_tokens for item in events),
        "output_tokens": sum(item.output_tokens for item in events),
        "retries": sum(item.retries for item in events),
        "fallback_count": sum(item.fallback_count for item in events),
        "estimated_cost_usd": round(sum(item.estimated_cost_usd for item in events), 8),
    }
    return {
        "schema_version": TRACE_SCHEMA_VERSION,
        "task_id": task_id,
        "privacy": "content_free",
        "events": [item.model_dump(mode="json") for item in events],
        "totals": totals,
    }


def append_state_event(state: Any, event: TraceEvent) -> None:
    """Append an external stage (such as export) to an existing content-free trace."""
    raw = state.metadata.get("trace", {})
    events = [
        TraceEvent.model_validate(item) for item in raw.get("events", [])
        if isinstance(item, dict)
    ]
    events.append(event)
    state.metadata["trace"] = trace_payload(state.task_id, events)
