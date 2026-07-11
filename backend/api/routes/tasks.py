from __future__ import annotations

import uuid
import shutil
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import ValidationError

from backend.api.schemas import (
  TaskCreateResponse, TaskDetailResponse, TaskListResponse, TaskReportResponse,
  TaskStatusResponse, WorkflowStepSummary,
)
from backend.api.task_store import APITaskStatus, task_store
from backend.core.config import get_settings
from backend.core.orchestrator import create_default_orchestrator
from backend.core.state import AnalysisStatus
from backend.exporters.report_exporter import ReportExporter
from backend.schemas.paper import PaperInput
from backend.schemas.report_config import ReportConfiguration
from backend.api.uploads import UploadValidationError, deduplication_key, save_validated_pdf

router = APIRouter(
  prefix="/api/tasks",
  tags=["tasks"],
)
logger = logging.getLogger(__name__)

SENSITIVE_METADATA_PARTS = ("api_key", "apikey", "token", "secret", "password", "authorization")


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
      result[key] = [item for item in raw_value if isinstance(item, (str, int, float, bool)) or item is None]
    elif isinstance(raw_value, (str, int, float, bool)) or raw_value is None:
      result[key] = raw_value
  return result


@router.get("", response_model=TaskListResponse)
def list_tasks(
  limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)
) -> TaskListResponse:
  records, total = task_store.list_tasks(limit=limit, offset=offset)
  return TaskListResponse(
    items=[TaskStatusResponse.model_validate(record.model_dump(by_alias=True)) for record in records],
    total=total, limit=limit, offset=offset,
  )


@router.post("/analyze", response_model=TaskCreateResponse)
def create_analysis_task(
  background_tasks: BackgroundTasks,
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
    report_config = ReportConfiguration.from_form(analysis_depth, target_audience,
                                                   report_template, custom_sections)
  except (ValueError, ValidationError) as exc:
    pdf_path.unlink(missing_ok=True)
    raise HTTPException(status_code=422, detail=str(exc)) from exc
  config_data = report_config.model_dump(mode="json")
  key = deduplication_key(saved.sha256, query, language, config_data)
  existing = task_store.find_active_by_dedup_key(key)
  if existing:
    pdf_path.unlink(missing_ok=True)
    return TaskCreateResponse(task_id=existing.task_id, status=existing.status,
      message="An identical active task already exists; reusing it.", deduplicated=True)

  try:
    task_store.create_task(
      task_id=task_id,
      input_pdf_path=str(pdf_path),
      metadata={"query": query, "language": language, "original_filename": file.filename,
                "report_configuration": config_data},
      file_sha256=saved.sha256, dedup_key=key,
    )
  except Exception as exc:
    pdf_path.unlink(missing_ok=True)
    logger.exception("Failed to persist task %s", task_id)
    raise HTTPException(status_code=500, detail="Failed to persist analysis task.") from exc

  background_tasks.add_task(
    run_analysis_task,
    task_id=task_id,
    pdf_path=str(pdf_path),
    query=query,
    language=language,
    report_configuration=config_data,
  )

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
  return TaskStatusResponse.model_validate(record.model_dump(by_alias=True))


@router.post("/{task_id}/retry", response_model=TaskCreateResponse)
def retry_task(task_id: str, background_tasks: BackgroundTasks) -> TaskCreateResponse:
  source = task_store.get_task(task_id)
  if source is None:
    raise HTTPException(status_code=404, detail="Task not found.")
  if source.status not in (APITaskStatus.FAILED, APITaskStatus.CANCELED):
    raise HTTPException(status_code=409, detail="Only failed or canceled tasks can be retried.")
  source_path = Path(source.input_pdf_path or "")
  if not source.input_pdf_path or not source_path.is_file():
    raise HTTPException(status_code=409, detail="The source PDF is unavailable; please upload it again.")
  new_id = f"task_{uuid.uuid4().hex[:12]}"
  new_path = source_path.with_name(f"{new_id}.pdf")
  try:
    shutil.copyfile(source_path, new_path)
    metadata = {**source.task_metadata, "retry_of": task_id}
    task_store.create_task(new_id, str(new_path), metadata, source.file_sha256,
                           source.dedup_key, retry_of=task_id)
  except Exception as exc:
    new_path.unlink(missing_ok=True)
    raise HTTPException(status_code=500, detail="Failed to create retry task.") from exc
  background_tasks.add_task(run_analysis_task, new_id, str(new_path),
                            str(source.task_metadata.get("query", "Analyze this paper.")),
                            source.task_metadata.get("language", "zh"),
                            source.task_metadata.get("report_configuration", {}))
  return TaskCreateResponse(task_id=new_id, status=APITaskStatus.PENDING,
                            message="Retry task created.")


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


@router.get("/{task_id}/artifacts/{format}")
def get_task_artifact(task_id: str,
                      format: Literal["markdown", "json", "html", "pdf", "docx"]):
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
    raise HTTPException(status_code=500, detail=f"Artifact generation failed: {exc}") from exc
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
              if not detail.paper_title and isinstance(paper_metadata.get("title"), str):
                detail.paper_title = paper_metadata["title"]
              authors = paper_metadata.get("authors")
              if isinstance(authors, list):
                detail.paper_authors = [author for author in authors if isinstance(author, str)]
          detail.workflow_status = state.get("status") if isinstance(state.get("status"), str) else None
          for source, target in (
            ("created_at", "workflow_created_at"), ("updated_at", "workflow_updated_at"),
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
              if not isinstance(raw_step, dict) or not isinstance(raw_step.get("step_name"), str):
                continue
              timestamp = None
              if isinstance(raw_step.get("timestamp"), str):
                try:
                  timestamp = datetime.fromisoformat(raw_step["timestamp"])
                except ValueError:
                  pass
              detail.step_history.append(WorkflowStepSummary(
                step_name=raw_step["step_name"],
                status=str(raw_step.get("status", "unknown")),
                timestamp=timestamp,
                message=raw_step.get("message") if isinstance(raw_step.get("message"), str) else None,
                metadata=_safe_metadata(raw_step.get("metadata")),
              ))
      except (OSError, json.JSONDecodeError):
        logger.exception("Failed to read state for task %s", task_id)
  return detail


def run_analysis_task(task_id: str, pdf_path: str, query: str, language: Literal["zh", "en"],
                      report_configuration: dict | None = None) -> None:
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
    state.metadata["structured_output_stats"] = getattr(llm_client, "structured_output_stats", {})

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
        "num_evidence_items": len(evidence_bundle.items) if evidence_bundle else 0,
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
