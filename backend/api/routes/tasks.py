from __future__ import annotations

import shutil
import uuid
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, UploadFile

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
) -> TaskCreateResponse:
  """
    Upload a PDF and create a background analysis task.
  """

  if not file.filename:
    raise HTTPException(status_code=400, detail="Uploaded file has no filename.")

  if not file.filename.lower().endswith(".pdf"):
    raise HTTPException(status_code=400, detail="Only PDF files are supported.")

  settings = get_settings()
  task_id = f"task_{uuid.uuid4().hex[:12]}"

  upload_dir = settings.resolve_path(settings.output_dir) / "uploads"
  upload_dir.mkdir(parents=True, exist_ok=True)

  pdf_path = upload_dir / f"{task_id}.pdf"

  try:
    with pdf_path.open("wb") as buffer:
      shutil.copyfileobj(file.file, buffer)
  except Exception as exc:
    raise HTTPException(status_code=500, detail="Failed to save uploaded PDF.") from exc
  finally:
    file.file.close()

  try:
    task_store.create_task(
      task_id=task_id,
      input_pdf_path=str(pdf_path),
      metadata={"query": query, "language": language, "original_filename": file.filename},
    )
  except Exception as exc:
    logger.exception("Failed to persist task %s", task_id)
    raise HTTPException(status_code=500, detail="Failed to persist analysis task.") from exc

  background_tasks.add_task(
    run_analysis_task,
    task_id=task_id,
    pdf_path=str(pdf_path),
    query=query,
    language=language,
  )

  return TaskCreateResponse(
    task_id=task_id,
    status=APITaskStatus.PENDING,
    message="Analysis task created.",
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


def run_analysis_task( task_id: str, pdf_path: str, query: str, language: Literal["zh", "en"],) -> None:
  """
    Background task function.

    It updates task_store as the analysis progresses.
  """

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
    )

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
    state_json_path = log_dir / f"{task_id}_state.json"

    exporter = ReportExporter()
    exporter.save_all(
      state=state,
      report_md_path=report_path,
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
      },
    )

  except Exception as exc:
    logger.exception("Analysis task %s failed", task_id)
    try:
      task_store.mark_failed(task_id=task_id, error_message=str(exc))
    except Exception:
      logger.exception("Failed to persist failure state for task %s", task_id)
