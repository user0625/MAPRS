from __future__ import annotations

import uuid
import asyncio
import shutil
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import ValidationError

from backend.api.schemas import (
    TaskCreateResponse,
    TaskDetailResponse,
    TaskListResponse,
    TaskReportResponse,
    StructuredReportResponse,
    EvidenceResponse,
    TaskStatusResponse,
    WorkflowStepSummary,
)
from backend.api.task_store import APITaskStatus, task_store
from backend.core.config import get_settings
from backend.core.orchestrator import create_default_orchestrator
from backend.core.state import AnalysisStatus
from backend.exporters.report_exporter import ReportExporter
from backend.schemas.paper import PaperInput
from backend.schemas.report_config import ReportConfiguration
from backend.api.uploads import (
    UploadValidationError,
    deduplication_key,
    save_validated_pdf,
)

router = APIRouter(
    prefix="/api/tasks",
    tags=["tasks"],
)
logger = logging.getLogger(__name__)

SENSITIVE_METADATA_PARTS = (
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
    "authorization",
)


def _safe_metadata(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, object] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        if any(part in key.lower() for part in SENSITIVE_METADATA_PARTS):
            continue
        if isinstance(raw_value, dict):
            result[key] = _safe_metadata(raw_value)
        elif isinstance(raw_value, list):
            result[key] = [
                item
                for item in raw_value
                if isinstance(item, (str, int, float, bool)) or item is None
            ]
        elif isinstance(raw_value, (str, int, float, bool)) or raw_value is None:
            result[key] = raw_value
    return result


@router.get("", response_model=TaskListResponse)
def list_tasks(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None),
    search: str | None = Query(None),
) -> TaskListResponse:
    records, total = task_store.list_tasks(
        limit=limit, offset=offset, status=status, search=search
    )
    return TaskListResponse(
        items=[
            TaskStatusResponse.model_validate(record.model_dump(by_alias=True))
            for record in records
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/analyze", response_model=TaskCreateResponse)
def create_analysis_task(
    file: UploadFile = File(...),
    query: str = Form("Analyze this paper and generate a structured reading report."),
    language: Literal["zh", "en"] = Form("zh"),
    analysis_depth: str = Form("standard"),
    target_audience: str = Form("researcher"),
    report_template: str = Form("standard"),
    custom_sections: str | None = Form(None),
) -> TaskCreateResponse:
    """
    Upload a PDF and create a background analysis task.
    """

    settings = get_settings()
    task_id = f"task_{uuid.uuid4().hex[:12]}"

    upload_dir = settings.resolve_path(settings.output_dir) / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = upload_dir / f"{task_id}.pdf"

    try:
        saved = save_validated_pdf(file, pdf_path, settings.max_upload_bytes)
    except UploadValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        report_config = ReportConfiguration.from_form(
            analysis_depth, target_audience, report_template, custom_sections
        )
    except (ValueError, ValidationError) as exc:
        pdf_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    config_data = report_config.model_dump(mode="json")
    key = deduplication_key(saved.sha256, query, language, config_data)
    existing = task_store.find_active_by_dedup_key(key)
    if existing and existing.input_pdf_path and Path(existing.input_pdf_path).parent == upload_dir:
        pdf_path.unlink(missing_ok=True)
        return TaskCreateResponse(
            task_id=existing.task_id,
            status=existing.status,
            message="An identical active task already exists; reusing it.",
            deduplicated=True,
        )

    try:
        task_store.create_task(
            task_id=task_id,
            input_pdf_path=str(pdf_path),
            metadata={
                "query": query,
                "language": language,
                "original_filename": file.filename,
                "report_configuration": config_data,
            },
            file_sha256=saved.sha256,
            dedup_key=key,
        )
    except Exception as exc:
        pdf_path.unlink(missing_ok=True)
        logger.exception("Failed to persist task %s", task_id)
        raise HTTPException(
            status_code=500, detail="Failed to persist analysis task."
        ) from exc

    from backend.worker.tasks import enqueue_analysis

    try:
        enqueue_analysis(task_id)
    except Exception as exc:
        task_store.mark_failed(task_id, "Task broker is unavailable.")
        raise HTTPException(
            status_code=503, detail="Task broker is unavailable."
        ) from exc

    return TaskCreateResponse(
        task_id=task_id,
        status=APITaskStatus.PENDING,
        message="Analysis task created.",
    )


@router.post("/{task_id}/cancel", response_model=TaskStatusResponse)
def cancel_task(task_id: str) -> TaskStatusResponse:
    record = task_store.request_cancel(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    if record.status == APITaskStatus.CANCELED and record.celery_task_id:
        from backend.worker.celery_app import celery_app

        celery_app.control.revoke(record.celery_task_id, terminate=False)
    return TaskStatusResponse.model_validate(record.model_dump(by_alias=True))


@router.post("/{task_id}/retry", response_model=TaskCreateResponse)
def retry_task(task_id: str) -> TaskCreateResponse:
    source = task_store.get_task(task_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    if source.status not in (APITaskStatus.FAILED, APITaskStatus.CANCELED):
        raise HTTPException(
            status_code=409, detail="Only failed or canceled tasks can be retried."
        )
    source_path = Path(source.input_pdf_path or "")
    if not source.input_pdf_path or not source_path.is_file():
        raise HTTPException(
            status_code=409,
            detail="The source PDF is unavailable; please upload it again.",
        )
    new_id = f"task_{uuid.uuid4().hex[:12]}"
    new_path = source_path.with_name(f"{new_id}.pdf")
    try:
        shutil.copyfile(source_path, new_path)
        metadata = {**source.task_metadata, "retry_of": task_id}
        task_store.create_task(
            new_id,
            str(new_path),
            metadata,
            source.file_sha256,
            source.dedup_key,
            retry_of=task_id,
        )
    except Exception as exc:
        new_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=500, detail="Failed to create retry task."
        ) from exc
    from backend.worker.tasks import enqueue_analysis

    enqueue_analysis(new_id)
    return TaskCreateResponse(
        task_id=new_id, status=APITaskStatus.PENDING, message="Retry task created."
    )


@router.post("/{task_id}/rerun", response_model=TaskCreateResponse)
def rerun_task(task_id: str) -> TaskCreateResponse:
    source = task_store.get_task(task_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    if source.status not in (
        APITaskStatus.COMPLETED,
        APITaskStatus.FAILED,
        APITaskStatus.CANCELED,
    ):
        raise HTTPException(status_code=409, detail="Only terminal tasks can be rerun.")
    source_path = Path(source.input_pdf_path or "")
    if not source_path.is_file():
        raise HTTPException(status_code=409, detail="The source PDF is unavailable.")
    new_id = f"task_{uuid.uuid4().hex[:12]}"
    new_path = source_path.with_name(f"{new_id}.pdf")
    shutil.copyfile(source_path, new_path)
    task_store.create_task(
        new_id,
        str(new_path),
        {**source.task_metadata, "rerun_of": task_id},
        source.file_sha256,
        rerun_of=task_id,
    )
    from backend.worker.tasks import enqueue_analysis

    enqueue_analysis(new_id)
    return TaskCreateResponse(
        task_id=new_id, status=APITaskStatus.PENDING, message="Rerun task created."
    )


@router.post("/{task_id}/resume", response_model=TaskStatusResponse)
def resume_task(task_id: str) -> TaskStatusResponse:
    source = task_store.get_task(task_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    if source.status not in (APITaskStatus.FAILED, APITaskStatus.INTERRUPTED):
        raise HTTPException(
            status_code=409, detail="Only failed or interrupted tasks can be resumed."
        )
    checkpoint = task_store.latest_checkpoint(task_id)
    settings = get_settings()
    if (
        not checkpoint
        or checkpoint.schema_version != settings.checkpoint_schema_version
    ):
        raise HTTPException(
            status_code=409, detail="No compatible checkpoint is available."
        )
    record = task_store.prepare_resume(task_id)
    from backend.worker.tasks import enqueue_analysis

    enqueue_analysis(task_id, resume=True)
    return TaskStatusResponse.model_validate(record.model_dump(by_alias=True))


@router.delete("/{task_id}", status_code=204)
def delete_task(task_id: str):
    record = task_store.get_task(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    if record.status not in (
        APITaskStatus.COMPLETED,
        APITaskStatus.FAILED,
        APITaskStatus.CANCELED,
    ):
        raise HTTPException(
            status_code=409, detail="Only terminal tasks can be deleted."
        )
    task_store.soft_delete(task_id)


def _sse(event) -> str:
    payload = {
        "id": event.sequence,
        "type": event.event_type,
        "status": event.status,
        "step": event.step,
        "message": event.message,
        "metadata": _safe_metadata(event.event_metadata),
        "created_at": event.created_at.isoformat(),
    }
    return f"id: {event.sequence}\nevent: {event.event_type}\ndata: {json.dumps(payload)}\n\n"


@router.get("/{task_id}/events")
async def task_events(
    task_id: str,
    after: int = Query(0, ge=0),
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
):
    record = task_store.get_task(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    try:
        cursor = max(after, int(last_event_id or 0))
    except ValueError:
        cursor = after

    async def stream():
        nonlocal cursor
        heartbeat = get_settings().sse_heartbeat_seconds
        while True:
            events = task_store.list_events(task_id, cursor)
            for event in events:
                cursor = event.sequence
                yield _sse(event)
                if event.event_type in {"completed", "failed", "canceled", "deleted"}:
                    return
            current = task_store.get_task(task_id, include_deleted=True)
            if current and current.status in (
                APITaskStatus.COMPLETED,
                APITaskStatus.FAILED,
                APITaskStatus.CANCELED,
            ):
                return
            yield f"event: heartbeat\ndata: {json.dumps({'after': cursor})}\n\n"
            await asyncio.sleep(heartbeat)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{task_id}", response_model=TaskStatusResponse)
def get_task_status(task_id: str) -> TaskStatusResponse:
    record = task_store.get_task(task_id)

    if record is None:
        raise HTTPException(status_code=404, detail="Task not found.")

    return TaskStatusResponse.model_validate(record.model_dump(by_alias=True))


@router.get("/{task_id}/report", response_model=TaskReportResponse)
def get_task_report(task_id: str) -> TaskReportResponse:
    record = task_store.get_task(task_id)

    if record is None:
        raise HTTPException(status_code=404, detail="Task not found.")

    if record.status != APITaskStatus.COMPLETED:
        raise HTTPException(
            status_code=409,
            detail=f"Task is not completed. Current status: {record.status}",
        )

    if not record.report_path:
        raise HTTPException(status_code=404, detail="Report path is missing.")

    report_path = Path(record.report_path)

    if not report_path.exists():
        raise HTTPException(status_code=404, detail="Report file does not exist.")

    report_markdown = report_path.read_text(encoding="utf-8")

    return TaskReportResponse(
        task_id=record.task_id,
        status=record.status,
        report_markdown=report_markdown,
        report_path=record.report_path,
    )


def _completed_state(task_id: str) -> tuple[object, dict]:
    record = task_store.get_task(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    if record.status != APITaskStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="Task is not completed.")
    if not record.state_json_path or not Path(record.state_json_path).is_file():
        raise HTTPException(
            status_code=404, detail="Structured analysis state is unavailable."
        )
    try:
        state = json.loads(Path(record.state_json_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=500, detail="Structured analysis state is invalid."
        ) from exc
    return record, state


@router.get("/{task_id}/report/structured", response_model=StructuredReportResponse)
def get_structured_report(task_id: str) -> StructuredReportResponse:
    record, state = _completed_state(task_id)
    report = state.get("final_report")
    if not isinstance(report, dict):
        report_path = Path(record.report_path or "").with_suffix(".json")
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise HTTPException(status_code=404, detail="Structured report is unavailable.")
    report = dict(report)
    sections = report.get("sections", [])
    if isinstance(sections, list):
        report["sections"] = sorted(
            (section for section in sections if isinstance(section, dict)),
            key=lambda section: section.get("order", 0),
        )
    evidence = state.get("evidence_bundle", {}).get("items", [])
    return StructuredReportResponse.model_validate({
        "task_id": task_id,
        "report": report,
        "quality_summary": report.get("quality_summary")
        or state.get("metadata", {}).get("quality_evaluation", {}),
        "evidence_index": [
            {
                k: item.get(k)
                for k in (
                    "evidence_id",
                    "chunk_id",
                    "page_start",
                    "page_end",
                    "section",
                )
            }
            for item in evidence
            if isinstance(item, dict)
        ],
    })


@router.get("/{task_id}/evidence/{evidence_id}", response_model=EvidenceResponse)
def get_evidence(task_id: str, evidence_id: str) -> EvidenceResponse:
    record, state = _completed_state(task_id)
    items = state.get("evidence_bundle", {}).get("items", [])
    for item in items:
        if isinstance(item, dict) and item.get("evidence_id") == evidence_id:
            if record.paper_id and item.get("paper_id") not in (None, record.paper_id):
                break
            return EvidenceResponse.model_validate({
                "task_id": task_id,
                "evidence_id": evidence_id,
                "chunk_id": item.get("chunk_id"),
                "page_start": item.get("page_start"),
                "page_end": item.get("page_end"),
                "section": item.get("section"),
                "text": str(item.get("text", ""))[:2000],
            })
    raise HTTPException(status_code=404, detail="Evidence not found for this task.")


@router.get("/{task_id}/artifacts/{format}")
def get_task_artifact(
    task_id: str, format: Literal["markdown", "json", "html", "pdf", "docx"]
):
    record = task_store.get_task(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    if record.status != APITaskStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="Task is not completed.")
    if not record.report_path or not Path(record.report_path).is_file():
        raise HTTPException(status_code=404, detail="Source report is missing.")
    from backend.exporters.artifact_exporter import ArtifactExporter

    try:
        path, media_type = ArtifactExporter().get_or_create(record, format)
    except (OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(
            status_code=500, detail=f"Artifact generation failed: {exc}"
        ) from exc
    return FileResponse(path, media_type=media_type, filename=path.name)


@router.get("/{task_id}/detail", response_model=TaskDetailResponse)
def get_task_detail(task_id: str) -> TaskDetailResponse:
    record = task_store.get_task(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Task not found.")

    detail = TaskDetailResponse.model_validate(record.model_dump(by_alias=True))
    if record.report_path:
        report_path = Path(record.report_path)
        if report_path.is_file():
            try:
                detail.report_markdown = report_path.read_text(encoding="utf-8")
                detail.report_available = True
            except OSError:
                logger.exception("Failed to read report for task %s", task_id)

    if record.state_json_path:
        state_path = Path(record.state_json_path)
        if state_path.is_file():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                if isinstance(state, dict):
                    detail.state_available = True
                    document = state.get("document")
                    if isinstance(document, dict):
                        paper_metadata = document.get("metadata")
                        if isinstance(paper_metadata, dict):
                            if not detail.paper_title and isinstance(
                                paper_metadata.get("title"), str
                            ):
                                detail.paper_title = paper_metadata["title"]
                            authors = paper_metadata.get("authors")
                            if isinstance(authors, list):
                                detail.paper_authors = [
                                    author
                                    for author in authors
                                    if isinstance(author, str)
                                ]
                    detail.workflow_status = (
                        state.get("status")
                        if isinstance(state.get("status"), str)
                        else None
                    )
                    for source, target in (
                        ("created_at", "workflow_created_at"),
                        ("updated_at", "workflow_updated_at"),
                        ("completed_at", "workflow_completed_at"),
                    ):
                        value = state.get(source)
                        if isinstance(value, str):
                            try:
                                setattr(detail, target, datetime.fromisoformat(value))
                            except ValueError:
                                pass
                    detail.workflow_metadata = _safe_metadata(state.get("metadata"))
                    raw_steps = state.get("step_history")
                    if isinstance(raw_steps, list):
                        detail.step_history = []
                        for raw_step in raw_steps:
                            if not isinstance(raw_step, dict) or not isinstance(
                                raw_step.get("step_name"), str
                            ):
                                continue
                            timestamp = None
                            if isinstance(raw_step.get("timestamp"), str):
                                try:
                                    timestamp = datetime.fromisoformat(
                                        raw_step["timestamp"]
                                    )
                                except ValueError:
                                    pass
                            detail.step_history.append(
                                WorkflowStepSummary(
                                    step_name=raw_step["step_name"],
                                    status=str(raw_step.get("status", "unknown")),
                                    timestamp=timestamp,
                                    message=raw_step.get("message")
                                    if isinstance(raw_step.get("message"), str)
                                    else None,
                                    metadata=_safe_metadata(raw_step.get("metadata")),
                                )
                            )
            except (OSError, json.JSONDecodeError):
                logger.exception("Failed to read state for task %s", task_id)
    return detail


def run_analysis_task(
    task_id: str,
    pdf_path: str,
    query: str,
    language: Literal["zh", "en"],
    report_configuration: dict | None = None,
) -> None:
    """
    Background task function.

    It updates task_store as the analysis progresses.
    """

    if task_store.is_cancel_requested(task_id):
        task_store.mark_canceled(task_id)
        return
    task_store.mark_running(
        task_id=task_id,
        message="Paper analysis is running.",
    )

    try:
        settings = get_settings()
        orchestrator = create_default_orchestrator(settings)

        paper_input = PaperInput(
            source_type="pdf",
            source_path=pdf_path,
            user_query=query,
        )

        state = orchestrator.run(
            paper_input=paper_input,
            output_language=language,
            cancel_check=lambda: task_store.is_cancel_requested(task_id),
            task_id=task_id,
            report_configuration=report_configuration,
        )

        state.metadata.update(getattr(orchestrator, "prompt_metadata", {}))
        llm_client = orchestrator.planner_agent.llm_client
        state.metadata["structured_output_stats"] = getattr(
            llm_client, "structured_output_stats", {}
        )

        if state.metadata.get("canceled"):
            log_dir = settings.resolve_path(settings.log_dir)
            log_dir.mkdir(parents=True, exist_ok=True)
            state_json_path = log_dir / f"{task_id}_state.json"
            ReportExporter().save_state_json(state, state_json_path)
            task_store.mark_canceled(task_id, str(state_json_path))
            return

        if state.status != AnalysisStatus.COMPLETED:
            log_dir = settings.resolve_path(settings.log_dir)
            state_json_path = log_dir / f"{task_id}_state.json"
            ReportExporter().save_state_json(state, state_json_path)
            task_store.mark_failed(
                task_id=task_id,
                error_message=state.error_message or "Paper analysis failed.",
                state_json_path=str(state_json_path),
            )
            return

        if state.final_report is None:
            task_store.mark_failed(
                task_id=task_id,
                error_message="Analysis completed but final report is missing.",
            )
            return

        report_dir = settings.resolve_path(settings.report_dir)
        log_dir = settings.resolve_path(settings.log_dir)

        report_dir.mkdir(parents=True, exist_ok=True)
        log_dir.mkdir(parents=True, exist_ok=True)

        report_path = report_dir / f"{task_id}_report.md"
        report_json_path = report_dir / f"{task_id}_report.json"
        state_json_path = log_dir / f"{task_id}_state.json"

        exporter = ReportExporter()
        exporter.save_all(
            state=state,
            report_md_path=report_path,
            report_json_path=report_json_path,
            state_json_path=state_json_path,
        )

        document = state.document
        evidence_bundle = state.evidence_bundle
        final_report = state.final_report

        task_store.mark_completed(
            task_id=task_id,
            report_path=str(report_path),
            state_json_path=str(state_json_path),
            paper_title=document.metadata.title if document else None,
            paper_id=document.metadata.paper_id if document else None,
            metadata={
                "paper_authors": document.metadata.authors if document else [],
                "num_pages": len(document.pages) if document else 0,
                "num_chunks": len(document.chunks) if document else 0,
                "num_evidence_items": len(evidence_bundle.items)
                if evidence_bundle
                else 0,
                "num_report_sections": len(final_report.sections),
                "metadata_quality": state.metadata.get("metadata_quality", {}),
                "paper_sections": state.metadata.get("paper_sections", []),
                "report_configuration": state.metadata.get("report_configuration", {}),
                "quality_evaluation": state.metadata.get("quality_evaluation", {}),
                "artifact_formats": ["markdown", "json", "html", "pdf", "docx"],
            },
        )

    except Exception as exc:
        logger.exception("Analysis task %s failed", task_id)
        try:
            task_store.mark_failed(task_id=task_id, error_message=str(exc))
        except Exception:
            logger.exception("Failed to persist failure state for task %s", task_id)
