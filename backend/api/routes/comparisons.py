from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Query, Response, status
from fastapi.responses import FileResponse, StreamingResponse

from backend.api import task_store as store_module
from backend.api.comparison_store import COMPARISON_TERMINAL, ComparisonStatus, comparison_store_for
from backend.api.schemas import (
    ComparisonCreate,
    ComparisonEvidenceResponse,
    ComparisonListResponse,
    ComparisonPaperResponse,
    ComparisonReportResponse,
    ComparisonResponse,
    ComparisonUpdate,
)
from backend.api.task_store import APITaskStatus
from backend.comparisons.exporter import MEDIA
from backend.core.config import get_settings


router = APIRouter(prefix="/api/comparisons", tags=["comparisons"])


def _store():
    return comparison_store_for(store_module.task_store)


def _response(record, include_papers: bool = True) -> ComparisonResponse:
    store = _store()
    papers = store.papers(record.id) if include_papers else []
    return ComparisonResponse(
        id=record.id,
        title=record.title,
        focus=record.focus,
        language=record.language,
        status=record.status,
        progress=record.progress,
        current_step=record.current_step,
        message=record.message,
        error_message=record.error_message,
        retry_of=record.retry_of,
        report_available=bool(record.report_path and Path(record.report_path).is_file()),
        structured_available=bool(record.structured_path and Path(record.structured_path).is_file()),
        artifact_formats=[item for item, raw in record.artifacts.items() if Path(raw).is_file()],
        last_event_id=record.last_event_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
        completed_at=record.completed_at,
        papers=[ComparisonPaperResponse(source_task_id=p.source_task_id, paper_id=p.paper_id, title=p.title, authors=p.authors, year=p.year, position=p.position) for p in papers],
    )


def _paper_snapshot(task_id: str) -> dict:
    task = store_module.task_store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Source task not found: {task_id}")
    if task.status != APITaskStatus.COMPLETED:
        raise HTTPException(status_code=409, detail=f"Source task is not completed: {task_id}")
    state_path = Path(task.state_json_path or "")
    report_path = Path(task.report_path or "")
    if not state_path.is_file() or not report_path.is_file():
        raise HTTPException(status_code=409, detail=f"Source state/report is unavailable: {task_id}")
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=f"Source state is invalid: {task_id}") from exc
    metadata = ((state.get("document") or {}).get("metadata") or {})
    return {
        "source_task_id": task_id,
        "paper_id": metadata.get("paper_id") or task.paper_id,
        "title": metadata.get("title") or task.paper_title or task_id,
        "authors": metadata.get("authors") or task.task_metadata.get("paper_authors") or [],
        "year": metadata.get("year"),
        "state_json_path": str(state_path),
    }


def _create(payload: ComparisonCreate, retry_of: str | None = None):
    papers = [_paper_snapshot(task_id) for task_id in payload.task_ids]
    comparison_id = f"cmp_{uuid.uuid4().hex[:12]}"
    title = payload.title.strip() if payload.title and payload.title.strip() else " vs. ".join(paper["title"] for paper in papers)[:200]
    record = _store().create(comparison_id, title, payload.focus.strip(), payload.language, papers, retry_of)
    from backend.worker.tasks import enqueue_comparison
    try:
        enqueue_comparison(comparison_id)
    except Exception as exc:
        _store().fail(comparison_id, "Comparison worker is unavailable.")
        raise HTTPException(status_code=503, detail="Comparison worker is unavailable.") from exc
    return record


@router.post("", response_model=ComparisonResponse, status_code=status.HTTP_202_ACCEPTED)
def create_comparison(payload: ComparisonCreate) -> ComparisonResponse:
    return _response(_create(payload))


@router.get("", response_model=ComparisonListResponse)
def list_comparisons(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0), search: str | None = None, status_filter: str | None = Query(None, alias="status")) -> ComparisonListResponse:
    rows, total = _store().list(limit, offset, status_filter, search)
    return ComparisonListResponse(items=[_response(row, include_papers=False) for row in rows], total=total, limit=limit, offset=offset)


@router.get("/{comparison_id}", response_model=ComparisonResponse)
def get_comparison(comparison_id: str) -> ComparisonResponse:
    record = _store().get(comparison_id)
    if not record:
        raise HTTPException(status_code=404, detail="Comparison not found.")
    return _response(record)


@router.patch("/{comparison_id}", response_model=ComparisonResponse)
def update_comparison(comparison_id: str, payload: ComparisonUpdate) -> ComparisonResponse:
    record = _store().update_title(comparison_id, payload.title.strip())
    if not record:
        raise HTTPException(status_code=404, detail="Comparison not found.")
    return _response(record)


@router.delete("/{comparison_id}", status_code=204)
def delete_comparison(comparison_id: str) -> Response:
    record = _store().get(comparison_id)
    if not record:
        raise HTTPException(status_code=404, detail="Comparison not found.")
    if record.status not in COMPARISON_TERMINAL:
        raise HTTPException(status_code=409, detail="Only terminal comparisons can be deleted.")
    _store().delete(comparison_id)
    return Response(status_code=204)


@router.post("/{comparison_id}/cancel", response_model=ComparisonResponse)
def cancel_comparison(comparison_id: str) -> ComparisonResponse:
    record = _store().cancel(comparison_id)
    if not record:
        raise HTTPException(status_code=404, detail="Comparison not found.")
    return _response(record)


@router.post("/{comparison_id}/retry", response_model=ComparisonResponse, status_code=status.HTTP_202_ACCEPTED)
def retry_comparison(comparison_id: str) -> ComparisonResponse:
    source = _store().get(comparison_id)
    if not source:
        raise HTTPException(status_code=404, detail="Comparison not found.")
    if source.status not in (ComparisonStatus.FAILED, ComparisonStatus.CANCELED):
        raise HTTPException(status_code=409, detail="Only failed or canceled comparisons can be retried.")
    task_ids = [paper.source_task_id for paper in _store().papers(comparison_id)]
    payload = ComparisonCreate(task_ids=task_ids, title=source.title, focus=source.focus, language=source.language)
    return _response(_create(payload, retry_of=comparison_id))


@router.get("/{comparison_id}/events")
async def comparison_events(comparison_id: str, after: int = Query(0, ge=0), last_event_id: str | None = Header(None, alias="Last-Event-ID")):
    if not _store().get(comparison_id):
        raise HTTPException(status_code=404, detail="Comparison not found.")
    try:
        cursor = max(after, int(last_event_id or 0))
    except ValueError:
        cursor = after

    async def stream():
        nonlocal cursor
        while True:
            for event in _store().events(comparison_id, cursor):
                cursor = event.sequence
                payload = {"id": event.sequence, "type": event.event_type, "status": event.status, "step": event.step, "message": event.message, "metadata": event.event_metadata, "created_at": event.created_at.isoformat()}
                yield f"id: {event.sequence}\nevent: {event.event_type}\ndata: {json.dumps(payload)}\n\n"
                if event.event_type in {"completed", "failed", "canceled"}:
                    return
            current = _store().get(comparison_id)
            if current and current.status in COMPARISON_TERMINAL:
                return
            yield f"event: heartbeat\ndata: {json.dumps({'after': cursor})}\n\n"
            await asyncio.sleep(get_settings().sse_heartbeat_seconds)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _completed(comparison_id: str):
    record = _store().get(comparison_id)
    if not record:
        raise HTTPException(status_code=404, detail="Comparison not found.")
    if record.status != ComparisonStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="Comparison is not completed.")
    return record


@router.get("/{comparison_id}/report", response_model=ComparisonReportResponse)
def comparison_report(comparison_id: str) -> ComparisonReportResponse:
    record = _completed(comparison_id)
    path = Path(record.report_path or "")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Comparison report is unavailable.")
    return ComparisonReportResponse(comparison_id=comparison_id, report_markdown=path.read_text(encoding="utf-8"), report_path=str(path))


@router.get("/{comparison_id}/report/structured")
def comparison_structured_report(comparison_id: str):
    record = _completed(comparison_id)
    path = Path(record.structured_path or "")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Structured comparison is unavailable.")
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("/{comparison_id}/evidence/{evidence_id}", response_model=ComparisonEvidenceResponse)
def comparison_evidence(comparison_id: str, evidence_id: str) -> ComparisonEvidenceResponse:
    item = _store().evidence(comparison_id, evidence_id)
    if not item:
        raise HTTPException(status_code=404, detail="Comparison evidence not found.")
    return ComparisonEvidenceResponse(comparison_id=comparison_id, **item.model_dump(exclude={"comparison_id", "created_at"}))


@router.get("/{comparison_id}/artifacts/{format}")
def comparison_artifact(comparison_id: str, format: str):
    if format not in MEDIA:
        raise HTTPException(status_code=404, detail="Unsupported artifact format.")
    record = _completed(comparison_id)
    path = Path(record.artifacts.get(format, ""))
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Comparison artifact is unavailable.")
    return FileResponse(path, media_type=MEDIA[format], filename=path.name)
