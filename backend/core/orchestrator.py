from __future__ import annotations

from typing import Literal
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import time
import uuid
import re

from backend.agents.critic_agent import CriticAgent
from backend.agents.base_agent import AgentError
from backend.agents.planner_agent import PlannerAgent
from backend.agents.reader_agent import ReaderAgent
from backend.agents.writer_agent import WriterAgent
from backend.agents.metadata_extractor_agent import MetadataExtractionInput, MetadataExtractorAgent
from backend.agents.verifier_agent import VerifierAgent, VerifierInput
from backend.core.state import AnalysisState, AnalysisStatus, StepStatus
from backend.schemas.agent_io import CriticInput, PlannerInput, ReaderInput, WriterInput
from backend.schemas.paper import PaperInput
from backend.tools.chunker import ChunkingError, DocumentChunker
from backend.tools.pdf_loader import PDFLoadError, PDFLoader
from backend.tools.retriever import PaperRetriever, RetrieverError
from backend.core.config import AppSettings
from backend.llm.client import create_llm_client
from backend.tools.embedder import MockEmbedder, OpenAICompatibleEmbedder
from backend.tools.vector_store import NumpyVectorStore
from backend.core.request_policy import RequestPolicy
from backend.llm.prompt_loader import PromptTemplateLoader
from backend.core.report_quality import ReportQualityGate
from backend.core.telemetry import (
  TraceEvent, estimate_cost, llm_snapshot, trace_payload, usage_delta,
)


class OrchestratorError(Exception):
  """Raised when the paper analysis workflow fails."""


class PaperAnalysisOrchestrator:
  """
    Orchestrates the full paper analysis workflow.

    This class coordinates tools and agents. It does not implement PDF parsing,
    chunking, retrieval, LLM calling, or report exporting by itself.
  """

  def __init__(
    self,
    pdf_loader: PDFLoader,
    chunker: DocumentChunker,
    retriever: PaperRetriever,
    planner_agent: PlannerAgent,
    reader_agent: ReaderAgent,
    critic_agent: CriticAgent,
    writer_agent: WriterAgent,
    metadata_extractor_agent: MetadataExtractorAgent | None = None,
    verifier_agent: VerifierAgent | None = None,
    settings: AppSettings | None = None,
    ) -> None:
    self.pdf_loader = pdf_loader
    self.chunker = chunker
    self.retriever = retriever
    self.planner_agent = planner_agent
    self.reader_agent = reader_agent
    self.critic_agent = critic_agent
    self.writer_agent = writer_agent
    self.metadata_extractor_agent = metadata_extractor_agent
    self.verifier_agent = verifier_agent
    self.settings = settings or AppSettings()
    self._trace_events: list[TraceEvent] = []
    self._cancel_check = None

  def run(self, paper_input: PaperInput, output_language: Literal["zh", "en"]="zh",
          cancel_check=None, task_id: str | None = None,
          report_configuration: dict | None = None, initial_state: AnalysisState | None = None,
          checkpoint_callback=None) -> AnalysisState:
    """
      Run the full analysis workflow.

      Parameters
      ----------
      paper_input:
          User-provided paper input.

      Returns
      -------
      AnalysisState
          Full workflow state containing intermediate outputs and final report.
    """

    state = initial_state or AnalysisState(
      task_id=task_id or self._generate_task_id(),
      paper_input=paper_input,
    )
    state.metadata["output_language"] = output_language
    config = report_configuration or {}
    state.metadata["report_configuration"] = config
    self._cancel_check = cancel_check
    existing_trace = state.metadata.get("trace", {}) if initial_state else {}
    self._trace_events = [
      TraceEvent.model_validate(item) for item in existing_trace.get("events", [])
      if isinstance(item, dict)
    ] if self.settings.analysis_trace_enabled else []

    try:
      steps = (self._parse_pdf, self._chunk_document, self._plan_analysis,
                   self._build_retrieval_index, self._retrieve_evidence,
                   self._read_paper, self._criticize_paper, self._write_report,
                   self._verify_report)
      completed = {item.step_name for item in state.step_history if item.status == StepStatus.SUCCESS}
      # The vector index is intentionally ephemeral and must be rebuilt on resume.
      if "build_retrieval_index" in completed and state.document and state.document.has_chunks():
        completed.discard("build_retrieval_index")
      for step in steps:
        if step.__name__.removeprefix("_") in completed:
          continue
        if cancel_check and cancel_check():
          state.metadata["canceled"] = True
          state.error_message = "Task canceled by user."
          self._record_cancellation(state)
          return state
        self._execute_traced_step(step, state)
        if checkpoint_callback:
          checkpoint_callback(step.__name__.removeprefix("_"), state)
        if cancel_check and cancel_check():
          state.metadata["canceled"] = True
          state.error_message = "Task canceled by user."
          self._record_cancellation(state)
          return state

      state.mark_completed()
      self._persist_trace(state)
      return state

    except Exception as exc:
      state.mark_failed(str(exc))
      self._persist_trace(state)
      return state

  def _execute_traced_step(self, step, state: AnalysisState) -> None:
    if not self.settings.analysis_trace_enabled:
      step(state)
      return
    client = self.reader_agent.llm_client
    before = llm_snapshot(client)
    warnings_before = {key for key in state.metadata if "warning" in key or "degraded" in key}
    started_wall = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    started = time.perf_counter()
    status = "success"
    error_class = None
    try:
      step(state)
    except Exception as exc:
      status, error_class = "failed", type(exc).__name__
      raise
    finally:
      after = llm_snapshot(client)
      delta = usage_delta(before, after)
      warnings_after = {key for key in state.metadata if "warning" in key or "degraded" in key}
      evidence_count = len(state.evidence_bundle.items) if state.evidence_bundle else 0
      self._trace_events.append(TraceEvent(
        stage=step.__name__.removeprefix("_"), status=status,
        started_at=started_wall, duration_ms=(time.perf_counter() - started) * 1000,
        model=getattr(client, "model_name", None),
        prompt_version=getattr(self, "prompt_metadata", {}).get("prompt_set_version", self.settings.prompt_set_version),
        input_tokens=delta["input_tokens"], output_tokens=delta["output_tokens"],
        retries=delta["retries"], fallback_count=len(warnings_after - warnings_before),
        estimated_cost_usd=estimate_cost(
          delta["input_tokens"], delta["output_tokens"],
          self.settings.llm_input_cost_per_million_usd,
          self.settings.llm_output_cost_per_million_usd,
        ),
        evidence_count=evidence_count, error_class=error_class,
      ))
      self._persist_trace(state)

  def _persist_trace(self, state: AnalysisState) -> None:
    if self.settings.analysis_trace_enabled:
      state.metadata["trace"] = trace_payload(state.task_id, self._trace_events)

  def _record_cancellation(self, state: AnalysisState) -> None:
    if self.settings.analysis_trace_enabled:
      self._trace_events.append(TraceEvent(
        stage="workflow", status="canceled",
        started_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        duration_ms=0, model=getattr(self.reader_agent.llm_client, "model_name", None),
        prompt_version=self.settings.prompt_set_version,
        evidence_count=len(state.evidence_bundle.items) if state.evidence_bundle else 0,
      ))
      self._persist_trace(state)

  def _parse_pdf(self, state: AnalysisState) -> None:
    state.update_status(AnalysisStatus.PARSING)

    try:
      document = self.pdf_loader.load(state.paper_input.source_path)
    except (PDFLoadError, FileNotFoundError, ValueError) as exc:
      raise OrchestratorError(f"PDF parsing failed: {exc}") from exc

    state.document = document
    if self.metadata_extractor_agent and not self.metadata_extractor_agent.is_mock:
      adjudicate = ["title", "authors", "venue"]
      requested = list(adjudicate)
      for name in ("title", "authors", "abstract", "year", "venue", "doi", "arxiv_id",
                   "language", "keywords"):
        value = getattr(document.metadata, name)
        provenance = document.metadata.fields.get(name)
        if value in (None, "", []) or provenance is None or provenance.confidence < .7:
          if name not in requested:
            requested.append(name)
      if requested:
        try:
          extracted = self.metadata_extractor_agent.run(MetadataExtractionInput(
            current_metadata=document.metadata,
            first_page_text=document.pages[0].text if document.pages else "",
            abstract_candidate=document.metadata.abstract,
            section_candidates=[section.name for section in document.sections],
            requested_fields=requested,
            adjudicate_fields=adjudicate,
          ))
          for name in requested:
            value = getattr(extracted, name)
            confidence = min(.85, extracted.confidence.get(name, .6))
            if (name in adjudicate and confidence >= .65
                and self._candidate_supported(name, value, document.metadata)):
              document.metadata.set_field(name, value, "llm", confidence, force=True)
            elif name not in adjudicate:
              document.metadata.set_field(name, value, "llm", confidence)
        except AgentError:
          state.metadata["metadata_extractor_warning"] = \
            "Metadata LLM fallback failed; deterministic metadata was preserved."
    state.metadata["metadata_quality"] = {
      name: field.model_dump(mode="json") for name, field in document.metadata.fields.items()
    }
    state.metadata["paper_sections"] = [section.model_dump(mode="json", exclude={"text"})
                                         for section in document.sections]
    state.metadata["document_parsing"] = self.pdf_loader.summarize_pages(
      document.pages, self.pdf_loader.layout_mode,
    )
    depth = state.metadata.get("report_configuration", {}).get("analysis_depth", "standard")
    settings = self.settings
    state.metadata["hierarchical_analysis"] = bool(
      depth == "deep" or len(document.pages) > settings.hierarchical_page_threshold
      or len(document.full_text()) > settings.hierarchical_char_threshold)

    state.add_step(
      step_name="parse_pdf",
      status=StepStatus.SUCCESS,
      message="PDF parsed successfully.",
      metadata={
        "paper_id": document.metadata.paper_id,
        "total_pages": document.metadata.total_pages,
        "num_pages": len(document.pages),
        "document_parsing": state.metadata["document_parsing"],
      },
    )

  def _chunk_document(self, state: AnalysisState) -> None:
    state.update_status(AnalysisStatus.CHUNKING)

    if state.document is None:
      raise OrchestratorError("Cannot chunk document before PDF parsing.")

    try:
      document = self.chunker.chunk(state.document)
    except ChunkingError as exc:
      raise OrchestratorError(f"Document chunking failed: {exc}") from exc

    state.document = document

    state.add_step(
      step_name="chunk_document",
      status=StepStatus.SUCCESS,
      message="Document chunked successfully.",
      metadata={
        "num_chunks": len(document.chunks),
      },
    )

  def _plan_analysis(self, state: AnalysisState) -> None:
    state.update_status(AnalysisStatus.PLANNING)

    if state.document is None:
      raise OrchestratorError("Cannot plan analysis before document parsing.")

    planner_input = PlannerInput(
      paper_metadata=state.document.metadata,
      user_query=state.paper_input.user_query,
    )

    analysis_plan = self.planner_agent.run(planner_input)

    state.analysis_plan = analysis_plan

    state.add_step(
      step_name="plan_analysis",
      status=StepStatus.SUCCESS,
      message="Analysis plan generated successfully.",
      metadata={
        "num_tasks": len(analysis_plan.tasks),
        "num_focus_questions": len(analysis_plan.focus_questions),
        "need_retrieval": analysis_plan.need_retrieval,
      },
    )

  def _build_retrieval_index(self, state: AnalysisState) -> None:
    state.update_status(AnalysisStatus.RETRIEVING)

    if state.document is None:
      raise OrchestratorError("Cannot build retriever before document parsing.")

    if not state.document.has_chunks():
      raise OrchestratorError("Cannot build retriever because document has no chunks.")

    try:
      self.retriever.build_index_from_document(state.document)
    except RetrieverError as exc:
      raise OrchestratorError(f"Retriever index building failed: {exc}") from exc

    state.add_step(
      step_name="build_retrieval_index",
      status=StepStatus.SUCCESS,
      message="Retrieval index built successfully.",
      metadata={
        "num_chunks": len(state.document.chunks),
      },
    )

  def _retrieve_evidence(self, state: AnalysisState) -> None:
    state.update_status(AnalysisStatus.RETRIEVING)

    if state.analysis_plan is None:
      raise OrchestratorError("Cannot retrieve evidence before analysis planning.")

    queries = state.analysis_plan.focus_questions

    if not queries:
      queries = [
        "What problem does the paper solve?",
        "What are the main contributions?",
        "What is the proposed method?",
        "What are the experiments and results?",
        "What are the limitations?",
      ]

    try:
      evidence_bundle = self.retriever.retrieve_many(
        queries=queries,
        top_k=5,
      )
    except RetrieverError as exc:
      raise OrchestratorError(f"Evidence retrieval failed: {exc}") from exc

    state.evidence_bundle = evidence_bundle

    state.add_step(
      step_name="retrieve_evidence",
      status=StepStatus.SUCCESS,
      message="Evidence retrieved successfully.",
      metadata={
        "num_queries": len(queries),
        "num_evidence_items": len(evidence_bundle.items),
      },
    )

  def _read_paper(self, state: AnalysisState) -> None:
    state.update_status(AnalysisStatus.READING)

    if state.document is None:
      raise OrchestratorError("Cannot run ReaderAgent before document parsing.")

    if state.analysis_plan is None:
      raise OrchestratorError("Cannot run ReaderAgent before analysis planning.")

    reader_input = ReaderInput(
      paper_metadata=state.document.metadata,
      chunks=state.document.chunks,
      analysis_plan=state.analysis_plan,
      evidence_bundle=state.evidence_bundle,
    )

    reader_tasks = [
      task for task in state.analysis_plan.tasks
      if task.assigned_to in (None, "reader")
    ]
    if self.settings.parallel_reader_enabled and len(reader_tasks) > 1:
      reader_notes = self._read_parallel(state, reader_input, reader_tasks)
    else:
      reader_notes = self.reader_agent.run(reader_input)
      state.metadata["reader_execution"] = {
        "mode": "serial", "configured_parallelism": self.settings.reader_parallelism,
        "branch_count": 1, "successful_branches": 1, "failed_branches": 0,
        "coverage_gaps": [],
      }

    state.reader_notes = reader_notes

    state.add_step(
      step_name="read_paper",
      status=StepStatus.SUCCESS,
      message="ReaderAgent completed successfully.",
      metadata={
        "num_contributions": len(reader_notes.main_contributions),
        "num_key_terms": len(reader_notes.key_terms),
        "execution": state.metadata.get("reader_execution", {}),
      },
    )

  def _read_parallel(self, state: AnalysisState, base_input: ReaderInput, tasks) -> object:
    """Run Reader-only Planner tasks concurrently and aggregate in plan order."""
    from backend.schemas.agent_io import AnalysisPlan, ReaderNotes

    def run_branch(position, task):
      started_wall = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
      started = time.perf_counter()
      attempts = 0
      error = None
      notes = None
      for attempt in range(self.settings.reader_branch_retries + 1):
        attempts = attempt + 1
        if self._cancel_check and self._cancel_check():
          error = "Canceled"
          break
        plan = AnalysisPlan(
          mode=base_input.analysis_plan.mode,
          tasks=[task],
          focus_questions=[task.description or task.name],
          required_sections=base_input.analysis_plan.required_sections,
          need_retrieval=base_input.analysis_plan.need_retrieval,
          notes=None,
        )
        try:
          notes = self.reader_agent.run(base_input.model_copy(update={"analysis_plan": plan}))
          error = None
          break
        except Exception as exc:
          error = type(exc).__name__
      return position, task.task_id, notes, attempts, error, started_wall, (time.perf_counter() - started) * 1000

    results = []
    executor = ThreadPoolExecutor(max_workers=self.settings.reader_parallelism, thread_name_prefix="reader")
    futures = [executor.submit(run_branch, position, task) for position, task in enumerate(tasks)]
    try:
      for future in as_completed(futures):
        results.append(future.result())
    finally:
      executor.shutdown(wait=True, cancel_futures=True)
    results.sort(key=lambda item: item[0])
    successful = [item for item in results if item[2] is not None]
    if not successful:
      raise OrchestratorError("All parallel Reader branches failed or were canceled.")
    gaps = [item[1] for item in results if item[2] is None]
    branch_records = []
    for _, task_id, notes, attempts, error, started_wall, duration_ms in results:
      branch_records.append({
        "branch_id": task_id, "status": "success" if notes is not None else "failed",
        "attempts": attempts, "error_class": error, "duration_ms": round(duration_ms, 3),
      })
      if self.settings.analysis_trace_enabled:
        self._trace_events.append(TraceEvent(
          stage="reader_branch", status="success" if notes is not None else "failed",
          started_at=started_wall, duration_ms=duration_ms,
          model=getattr(self.reader_agent.llm_client, "model_name", None),
          prompt_version=self.settings.prompt_set_version,
          retries=max(0, attempts - 1), evidence_count=len(base_input.evidence_bundle.items)
          if base_input.evidence_bundle else 0,
          branch_id=task_id, error_class=error,
          metadata={"position": next(item[0] for item in results if item[1] == task_id)},
        ))
    state.metadata["reader_execution"] = {
      "mode": "parallel", "configured_parallelism": self.settings.reader_parallelism,
      "branch_count": len(tasks), "successful_branches": len(successful),
      "failed_branches": len(gaps), "coverage_gaps": gaps, "branches": branch_records,
    }
    notes_list = [item[2] for item in successful]

    def join_text(name):
      return "\n\n".join(dict.fromkeys(
        value for notes in notes_list if (value := getattr(notes, name))
      ))

    def merge_list(name):
      return list(dict.fromkeys(value for notes in notes_list for value in getattr(notes, name)))

    return ReaderNotes(
      problem_statement=join_text("problem_statement"), background=join_text("background"),
      main_contributions=merge_list("main_contributions"), method_summary=join_text("method_summary"),
      experiment_summary=join_text("experiment_summary"), conclusion_summary=join_text("conclusion_summary"),
      key_terms=merge_list("key_terms"), important_evidence_ids=merge_list("important_evidence_ids"),
    )

  def _criticize_paper(self, state: AnalysisState) -> None:
    state.update_status(AnalysisStatus.CRITICIZING)

    if state.document is None:
      raise OrchestratorError("Cannot run CriticAgent before document parsing.")

    if state.reader_notes is None:
      raise OrchestratorError("Cannot run CriticAgent before ReaderAgent.")

    critic_input = CriticInput(
      paper_metadata=state.document.metadata,
      reader_notes=state.reader_notes,
      evidence_bundle=state.evidence_bundle,
    )

    critic_notes = self.critic_agent.run(critic_input)

    state.critic_notes = critic_notes

    state.add_step(
      step_name="criticize_paper",
      status=StepStatus.SUCCESS,
      message="CriticAgent completed successfully.",
      metadata={
        "num_strengths": len(critic_notes.strengths),
        "num_limitations": len(critic_notes.limitations),
        "num_missing_experiments": len(critic_notes.missing_experiments),
      },
    )

  def _write_report(self, state: AnalysisState) -> None:
    state.update_status(AnalysisStatus.WRITING)

    if state.document is None:
      raise OrchestratorError("Cannot run WriterAgent before document parsing.")

    if state.analysis_plan is None:
      raise OrchestratorError("Cannot run WriterAgent before analysis planning.")

    if state.reader_notes is None:
      raise OrchestratorError("Cannot run WriterAgent before ReaderAgent.")

    if state.critic_notes is None:
      raise OrchestratorError("Cannot run WriterAgent before CriticAgent.")

    output_language = state.metadata.get("output_language", "zh")
    configuration = state.metadata.get("report_configuration", {})
    writer_input = WriterInput(
      paper_metadata=state.document.metadata,
      analysis_plan=state.analysis_plan,
      reader_notes=state.reader_notes,
      critic_notes=state.critic_notes,
      evidence_bundle=state.evidence_bundle,
      output_language=output_language,
      analysis_depth=configuration.get("analysis_depth", "standard"),
      target_audience=configuration.get("target_audience", "researcher"),
      report_template=configuration.get("report_template", "standard"),
      custom_sections=configuration.get("custom_sections", []),
    )

    final_report = self.writer_agent.run(writer_input)

    coverage_gaps = state.metadata.get("reader_execution", {}).get("coverage_gaps", [])
    if coverage_gaps:
      gap_warning = (
        "Parallel Reader coverage is incomplete; failed branches: "
        + ", ".join(coverage_gaps)
        + ". Verify the affected topics against the paper."
      )
      final_report.warning = " ".join(filter(None, [final_report.warning, gap_warning]))

    state.final_report = final_report

    state.add_step(
      step_name="write_report",
      status=StepStatus.SUCCESS,
      message="WriterAgent completed successfully.",
      metadata={
        "num_sections": len(final_report.sections),
        "has_markdown": bool(final_report.to_markdown()),
      },
    )

  def _verify_report(self, state: AnalysisState) -> None:
    if state.final_report is None or state.document is None:
      raise OrchestratorError("Cannot verify a missing report or document.")
    settings = self.settings
    gate = ReportQualityGate(settings.quality_pass_score, settings.citation_validity_min_score)
    summary = gate.evaluate(state.final_report, state.evidence_bundle, state.document)
    revision_instructions = list(summary.issues)
    llm_result = None
    if (settings.verifier_enabled and self.verifier_agent
        and not self.verifier_agent.is_mock):
      try:
        llm_result = self.verifier_agent.run(VerifierInput(
          report=state.final_report, evidence_bundle=state.evidence_bundle,
          deterministic_issues=summary.issues, pass_score=settings.quality_pass_score,
          citation_score=settings.citation_validity_min_score))
      except AgentError:
        state.metadata["verifier_warning"] = \
          "LLM Verifier failed; deterministic verification remained active."
        llm_result = None
    if llm_result is not None:
      revision_instructions.extend(llm_result.revision_instructions)
      summary.accuracy = llm_result.accuracy
      summary.completeness = llm_result.completeness
      summary.faithfulness = llm_result.faithfulness
      summary.citation_validity = min(summary.citation_validity, llm_result.citation_validity)
      summary.critical_depth = llm_result.critical_depth
      summary.overall = llm_result.overall
      summary.issues = (summary.issues + [issue.description for issue in llm_result.issues])[:20]
      summary.passed = bool(summary.passed and llm_result.passed
                            and summary.overall >= settings.quality_pass_score
                            and summary.citation_validity >= settings.citation_validity_min_score)
    if settings.verifier_enabled and not summary.passed:
      gate.sanitize(state.final_report, state.evidence_bundle)
      writer_input = self._build_writer_input(state)
      try:
        state.final_report = self.writer_agent.revise(
          writer_input, state.final_report, list(dict.fromkeys(revision_instructions))[:20])
      except AgentError:
        state.metadata["revision_warning"] = \
          "Writer revision failed; the original report was preserved."
      summary = gate.evaluate(state.final_report, state.evidence_bundle, state.document, revision_count=1)
      if self.verifier_agent and not self.verifier_agent.is_mock:
        try:
          llm_result = self.verifier_agent.run(VerifierInput(
            report=state.final_report, evidence_bundle=state.evidence_bundle,
            deterministic_issues=summary.issues, pass_score=settings.quality_pass_score,
            citation_score=settings.citation_validity_min_score))
        except AgentError:
          llm_result = None
      if llm_result is not None:
        summary.accuracy = llm_result.accuracy
        summary.completeness = llm_result.completeness
        summary.faithfulness = llm_result.faithfulness
        summary.citation_validity = min(summary.citation_validity, llm_result.citation_validity)
        summary.critical_depth = llm_result.critical_depth
        summary.overall = llm_result.overall
        summary.issues = (summary.issues + [issue.description for issue in llm_result.issues])[:20]
        summary.passed = bool(summary.passed and llm_result.passed
                              and summary.overall >= settings.quality_pass_score
                              and summary.citation_validity >= settings.citation_validity_min_score)
    state.final_report.quality_summary = summary
    state.metadata["quality_evaluation"] = summary.model_dump(mode="json")
    if not summary.passed:
      quality_warning = (llm_result.user_warning if llm_result else None) or \
        "报告未完全通过质量门禁；请结合原文核对未解决问题。"
      state.final_report.warning = " ".join(filter(None, [state.final_report.warning, quality_warning]))
    state.final_report.markdown_content = None
    state.add_step(step_name="verify_report", status=StepStatus.SUCCESS,
      message="Report citation and quality checks completed.",
      metadata={"overall": summary.overall, "passed": summary.passed,
                "revision_count": summary.revision_count})

  def _build_writer_input(self, state: AnalysisState) -> WriterInput:
    if not all((state.document, state.analysis_plan, state.reader_notes, state.critic_notes)):
      raise OrchestratorError("Writer revision inputs are incomplete.")
    configuration = state.metadata.get("report_configuration", {})
    return WriterInput(paper_metadata=state.document.metadata,
      analysis_plan=state.analysis_plan, reader_notes=state.reader_notes,
      critic_notes=state.critic_notes, evidence_bundle=state.evidence_bundle,
      output_language=state.metadata.get("output_language", "zh"),
      analysis_depth=configuration.get("analysis_depth", "standard"),
      target_audience=configuration.get("target_audience", "researcher"),
      report_template=configuration.get("report_template", "standard"),
      custom_sections=configuration.get("custom_sections", []))

  @staticmethod
  def _candidate_supported(name: str, value, metadata) -> bool:
    """Reject free-form model inventions before merging adjudicated fields."""
    if value in (None, "", []):
      return False
    candidate_text = " ".join(candidate.text for candidate in metadata.candidates)
    def normalize(text) -> list[str]:
      return re.findall(r"[\w-]+", str(text).casefold())
    available = set(normalize(candidate_text))
    values = value if isinstance(value, list) else [value]
    for item in values:
      tokens = normalize(item)
      if not tokens or sum(token in available for token in tokens) / len(tokens) < .9:
        return False
    joined = " ".join(str(item) for item in values).casefold()
    if name == "title":
      return (len(joined) <= 300 and not joined.startswith("arxiv:")
              and not re.fullmatch(r"(?:19|20)\d{2}.*", joined))
    if name == "authors":
      banned = ("university", "department", "institute", "laboratory", "abstract", "@")
      if any(word in joined for word in banned):
        return False
      title_tokens = set(normalize(metadata.title or ""))
      author_tokens = set(normalize(joined))
      if author_tokens and len(author_tokens & title_tokens) / len(author_tokens) > .7:
        return False
      return True
    return len(joined) <= 200

  def _generate_task_id(self) -> str:
    return f"task_{uuid.uuid4().hex[:12]}"
  





#工厂函数，便于cli使用
def create_default_orchestrator(settings: AppSettings) -> PaperAnalysisOrchestrator:
  """
    Create default orchestrator from AppSettings.

    This is useful for CLI/API entrypoints.
  """

  llm_client = create_llm_client(settings)

  if settings.embedding_provider == "mock":
    embedder = MockEmbedder(
      dimension=128,
      model_name=settings.embedding_model,
    )
  elif settings.embedding_provider == "openai_compatible":
    if not settings.embedding_api_key:
      raise ValueError(
        "embedding_api_key is required for openai_compatible embedding provider."
      )

    embedder = OpenAICompatibleEmbedder(
      api_key=settings.embedding_api_key,
      base_url=settings.embedding_base_url,
      model_name=settings.embedding_model,
      request_policy=RequestPolicy.from_settings(settings),
      timeout=(settings.request_connect_timeout, settings.request_read_timeout),
    )
  else:
    raise ValueError(f"Unsupported embedding_provider: {settings.embedding_provider}")

  retriever = PaperRetriever(
    embedder=embedder,
    vector_store=NumpyVectorStore(),
  )

  orchestrator = PaperAnalysisOrchestrator(
    pdf_loader=PDFLoader(layout_mode=settings.pdf_layout_mode),
    chunker=DocumentChunker(
      chunk_size=settings.chunk_size,
      chunk_overlap=settings.chunk_overlap,
    ),
    retriever=retriever,
    planner_agent=PlannerAgent(llm_client=llm_client),
    reader_agent=ReaderAgent(llm_client=llm_client),
    critic_agent=CriticAgent(llm_client=llm_client),
    writer_agent=WriterAgent(llm_client=llm_client),
    metadata_extractor_agent=MetadataExtractorAgent(llm_client=llm_client),
    verifier_agent=VerifierAgent(llm_client=llm_client),
    settings=settings,
  )
  loader = PromptTemplateLoader()
  hashes = loader.template_hashes()
  orchestrator.prompt_metadata = {"prompt_set_version": settings.prompt_set_version,
                                  "prompt_template_hashes": hashes}
  return orchestrator
