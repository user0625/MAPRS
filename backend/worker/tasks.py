from __future__ import annotations

import logging

from backend.api import task_store as store_module
from backend.api.task_store import DatabaseTaskStore
from backend.core.config import get_settings
from backend.core.orchestrator import create_default_orchestrator
from backend.core.state import AnalysisState, AnalysisStatus
from backend.exporters.report_exporter import ReportExporter
from backend.schemas.paper import PaperInput
from backend.worker.celery_app import celery_app
from backend.ask_retrieval import prebuild_retrieval_index

logger = logging.getLogger(__name__)
PROGRESS = {
    "parse_pdf": 10,
    "chunk_document": 20,
    "plan_analysis": 30,
    "build_retrieval_index": 40,
    "retrieve_evidence": 50,
    "read_paper": 65,
    "criticize_paper": 75,
    "write_report": 85,
    "verify_report": 95,
}


def get_store() -> DatabaseTaskStore:
    settings = get_settings()
    if store_module.task_store.database_url != settings.database_url:
        store_module.task_store = DatabaseTaskStore(settings.database_url)
    return store_module.task_store


def execute_analysis(task_id: str, resume: bool = False) -> None:
    settings = get_settings()
    store = get_store()
    record = store.get_task(task_id)
    if not record or not store.claim_task(task_id):
        return
    initial_state = None
    if resume:
        checkpoint = store.latest_checkpoint(task_id)
        if (
            not checkpoint
            or checkpoint.schema_version != settings.checkpoint_schema_version
        ):
            store.mark_failed(task_id, "Checkpoint is missing or incompatible.")
            return
        initial_state = AnalysisState.model_validate(checkpoint.state)
    try:
        orchestrator = create_default_orchestrator(settings)

        def checkpoint(step: str, state: AnalysisState) -> None:
            store.save_checkpoint(
                task_id,
                step,
                state.model_dump(mode="json"),
                settings.checkpoint_schema_version,
                PROGRESS[step],
            )

        state = orchestrator.run(
            PaperInput(
                source_type="pdf",
                source_path=record.input_pdf_path or "",
                user_query=str(
                    record.task_metadata.get("query", "Analyze this paper.")
                ),
            ),
            output_language=record.task_metadata.get("language", "zh"),
            task_id=task_id,
            report_configuration=record.task_metadata.get("report_configuration", {}),
            cancel_check=lambda: store.is_cancel_requested(task_id),
            initial_state=initial_state,
            checkpoint_callback=checkpoint,
        )
        state.metadata.update(getattr(orchestrator, "prompt_metadata", {}))
        if state.metadata.get("canceled"):
            store.mark_canceled(task_id)
            return
        if state.status != AnalysisStatus.COMPLETED or not state.final_report:
            store.mark_failed(task_id, state.error_message or "Paper analysis failed.")
            return
        report_dir = settings.resolve_path(settings.report_dir)
        log_dir = settings.resolve_path(settings.log_dir)
        report_dir.mkdir(parents=True, exist_ok=True)
        log_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / f"{task_id}_report.md"
        report_json_path = report_dir / f"{task_id}_report.json"
        state_path = log_dir / f"{task_id}_state.json"
        ReportExporter().save_all(state, report_path, report_json_path, state_path)
        index_metadata = prebuild_retrieval_index(task_id, state_path, settings)
        state.metadata["ask_retrieval_index"] = index_metadata
        ReportExporter().save_state_json(state, state_path)
        document = state.document
        store.mark_completed(
            task_id,
            str(report_path),
            str(state_path),
            document.metadata.title if document else None,
            document.metadata.paper_id if document else None,
            {
                "paper_authors": document.metadata.authors if document else [],
                "num_pages": len(document.pages) if document else 0,
                "num_chunks": len(document.chunks) if document else 0,
                "num_evidence_items": len(state.evidence_bundle.items)
                if state.evidence_bundle
                else 0,
                "num_report_sections": len(state.final_report.sections),
                "quality_evaluation": state.metadata.get("quality_evaluation", {}),
                "artifact_formats": ["markdown", "json", "html", "pdf", "docx"],
                "ask_retrieval_index": index_metadata,
            },
        )
    except Exception as exc:
        logger.exception("Analysis task %s failed", task_id)
        store.mark_failed(task_id, str(exc))


@celery_app.task(
    bind=True, autoretry_for=(ConnectionError, TimeoutError), retry_backoff=True
)
def analyze_paper(self, task_id: str, resume: bool = False) -> None:
    execute_analysis(task_id, resume)


def enqueue_analysis(task_id: str, resume: bool = False) -> str:
    result = analyze_paper.apply_async(args=[task_id, resume])
    get_store().set_celery_task_id(task_id, result.id)
    return result.id


@celery_app.task(
    bind=True, autoretry_for=(ConnectionError, TimeoutError), retry_backoff=True
)
def answer_paper_question(self, message_id: str) -> None:
    from backend.ask_paper import execute_answer

    execute_answer(message_id)


def enqueue_answer(message_id: str) -> str:
    result = answer_paper_question.apply_async(args=[message_id])
    return result.id


def execute_comparison(comparison_id: str) -> None:
    from backend.api.comparison_store import comparison_store_for
    from backend.comparisons.exporter import ComparisonExporter
    from backend.comparisons.service import build_comparison

    settings = get_settings()
    store = comparison_store_for(get_store())
    if not store.claim(comparison_id):
        return
    try:
        store.progress(comparison_id, "load_sources", 10, "Loading source states.")
        if store.is_cancel_requested(comparison_id):
            return
        store.progress(comparison_id, "retrieve_evidence", 35, "Retrieving cross-paper evidence.")
        structured, evidence = build_comparison(comparison_id, store, settings)
        if store.is_cancel_requested(comparison_id):
            return
        store.save_evidence(evidence)
        store.progress(comparison_id, "validate_citations", 75, "Validating citation whitelist.")
        output_dir = settings.resolve_path(settings.report_dir) / "comparisons"
        artifacts = ComparisonExporter().save_all(structured, output_dir, comparison_id)
        store.complete(comparison_id, artifacts["markdown"], artifacts["json"], artifacts)
    except Exception as exc:
        logger.exception("Comparison %s failed", comparison_id)
        store.fail(comparison_id, str(exc))


@celery_app.task(bind=True, autoretry_for=(ConnectionError, TimeoutError), retry_backoff=True)
def compare_papers(self, comparison_id: str) -> None:
    execute_comparison(comparison_id)


def enqueue_comparison(comparison_id: str) -> str:
    result = compare_papers.apply_async(args=[comparison_id])
    return result.id
