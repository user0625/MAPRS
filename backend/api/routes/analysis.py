from __future__ import annotations

import shutil
import uuid
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from backend.api.schemas import AnalyzeUploadResponse
from backend.core.config import get_settings
from backend.core.orchestrator import create_default_orchestrator
from backend.core.state import AnalysisStatus
from backend.exporters.report_exporter import ReportExporter
from backend.schemas.paper import PaperInput

router = APIRouter(
  prefix="/api/analyze",
  tags=["analysis"]
)


@router.post("/upload", response_model=AnalyzeUploadResponse)
def analyze_uploaded_pdf(
    file: UploadFile = File(...),
    query: str = Form("Analyze this paper and generate a structured reading report."),
    language: Literal["zh", "en"] = Form("zh"),
) -> AnalyzeUploadResponse:
  """
    Upload a PDF paper and generate a structured reading report.
    This is a synchronous MVP endpoint.
  """
  if not file.filename:
    raise HTTPException(status_code=400, detail="Uploaded file has no filename.")
  if not file.filename.lower().endswith(".pdf"):
    raise HTTPException(status_code=400, detail="only PDF files has supported.")

  settings = get_settings()

  upload_dir = settings.resolve_path(settings.output_dir) / "uploads"
  report_dir = settings.resolve_path(settings.report_dir)
  log_dir = settings.resolve_path(settings.log_dir)

  upload_dir.mkdir(parents=True, exist_ok=True)
  report_dir.mkdir(parents=True, exist_ok=True)
  log_dir.mkdir(parents=True, exist_ok=True)

  task_id = f"api_{uuid.uuid4().hex[:12]}"
  safe_pdf_path = upload_dir / f"{task_id}.pdf"

  try:
    with safe_pdf_path.open("wb") as buffer:
      shutil.copyfileobj(file.file, buffer)
  except Exception:
    raise HTTPException(status_code=500, detail="Failed to save uploaded PDF")
  finally:
    file.file.close()

  orchestrator = create_default_orchestrator(settings)

  paper_input = PaperInput(source_type="pdf", source_path=str(safe_pdf_path), user_query=query)

  state = orchestrator.run(paper_input=paper_input, output_language=language)

  if state.status != AnalysisStatus.COMPLETED :
    raise HTTPException(status_code=500, detail=state.error_message or "Paper analysis failed")

  if state.final_report is None:
    raise HTTPException(
      status_code=500, detail="Analysis completed but final report is missing."
    )

  report_path = report_dir / f"{task_id}_report.md"
  state_path = log_dir / f"{task_id}_state.json"

  exporter = ReportExporter()
  exporter.save_all(state=state, report_md_path=report_path, state_json_path=state_path)

  document = state.document
  evidence_bundle = state.evidence_bundle
  final_report = state.final_report

  return AnalyzeUploadResponse(
    task_id=task_id,
    status=state.status.value if hasattr(state.status, "value") else str(state.status),
    paper_title=document.metadata.title if document else None,
    paper_id=document.metadata.paper_id if document else None,
    report_markdown=final_report.to_markdown(),
    report_path=str(report_path),
    state_summary_path=str(state_path),
    num_pages=len(document.pages) if document else 0,
    num_chunks=len(document.chunks) if document else 0,
    num_evidence_items=len(evidence_bundle.items) if evidence_bundle else 0,
    num_report_sections=len(final_report.sections),
  )
