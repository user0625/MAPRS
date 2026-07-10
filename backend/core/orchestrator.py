from __future__ import annotations

from typing import Literal
import uuid

from backend.agents.critic_agent import CriticAgent
from backend.agents.planner_agent import PlannerAgent
from backend.agents.reader_agent import ReaderAgent
from backend.agents.writer_agent import WriterAgent
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
    ) -> None:
    self.pdf_loader = pdf_loader
    self.chunker = chunker
    self.retriever = retriever
    self.planner_agent = planner_agent
    self.reader_agent = reader_agent
    self.critic_agent = critic_agent
    self.writer_agent = writer_agent

  def run(self, paper_input: PaperInput, output_language: Literal["zh", "en"]="zh") -> AnalysisState:
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

    state = AnalysisState(
      task_id=self._generate_task_id(),
      paper_input=paper_input,
    )
    state.metadata["output_langugae"] = output_language

    try:
      self._parse_pdf(state)
      self._chunk_document(state)
      self._plan_analysis(state)
      self._build_retrieval_index(state)
      self._retrieve_evidence(state)
      self._read_paper(state)
      self._criticize_paper(state)
      self._write_report(state)

      state.mark_completed()
      return state

    except Exception as exc:
      state.mark_failed(str(exc))
      return state

  def _parse_pdf(self, state: AnalysisState) -> None:
    state.update_status(AnalysisStatus.PARSING)

    try:
      document = self.pdf_loader.load(state.paper_input.source_path)
    except (PDFLoadError, FileNotFoundError, ValueError) as exc:
      raise OrchestratorError(f"PDF parsing failed: {exc}") from exc

    state.document = document

    state.add_step(
      step_name="parse_pdf",
      status=StepStatus.SUCCESS,
      message="PDF parsed successfully.",
      metadata={
        "paper_id": document.metadata.paper_id,
        "total_pages": document.metadata.total_pages,
        "num_pages": len(document.pages),
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

    reader_notes = self.reader_agent.run(reader_input)

    state.reader_notes = reader_notes

    state.add_step(
      step_name="read_paper",
      status=StepStatus.SUCCESS,
      message="ReaderAgent completed successfully.",
      metadata={
        "num_contributions": len(reader_notes.main_contributions),
        "num_key_terms": len(reader_notes.key_terms),
      },
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
    writer_input = WriterInput(
      paper_metadata=state.document.metadata,
      analysis_plan=state.analysis_plan,
      reader_notes=state.reader_notes,
      critic_notes=state.critic_notes,
      evidence_bundle=state.evidence_bundle,
      output_language=output_language,
    )

    final_report = self.writer_agent.run(writer_input)

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
    )
  else:
    raise ValueError(f"Unsupported embedding_provider: {settings.embedding_provider}")

  retriever = PaperRetriever(
    embedder=embedder,
    vector_store=NumpyVectorStore(),
  )

  return PaperAnalysisOrchestrator(
    pdf_loader=PDFLoader(),
    chunker=DocumentChunker(
      chunk_size=settings.chunk_size,
      chunk_overlap=settings.chunk_overlap,
    ),
    retriever=retriever,
    planner_agent=PlannerAgent(llm_client=llm_client),
    reader_agent=ReaderAgent(llm_client=llm_client),
    critic_agent=CriticAgent(llm_client=llm_client),
    writer_agent=WriterAgent(llm_client=llm_client),
  )