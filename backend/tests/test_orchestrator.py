from pathlib import Path

import pytest

from backend.agents.critic_agent import CriticAgent
from backend.agents.planner_agent import PlannerAgent
from backend.agents.reader_agent import ReaderAgent
from backend.agents.writer_agent import WriterAgent
from backend.core.orchestrator import PaperAnalysisOrchestrator
from backend.core.state import AnalysisStatus
from backend.llm.client import MockLLMClient
from backend.schemas.paper import PaperInput
from backend.tools.chunker import DocumentChunker
from backend.tools.embedder import MockEmbedder
from backend.tools.pdf_loader import PDFLoader
from backend.tools.retriever import PaperRetriever
from backend.tools.vector_store import NumpyVectorStore

from backend.core.config import AppSettings
from backend.core.orchestrator import PaperAnalysisOrchestrator, create_default_orchestrator



@pytest.mark.skipif(
    not Path("backend/data/raw/example.pdf").exists(),
    reason="Test PDF does not exist.",
)
def test_orchestrator_runs_full_mock_workflow():
    llm_client = MockLLMClient()

    retriever = PaperRetriever(
        embedder=MockEmbedder(dimension=64),
        vector_store=NumpyVectorStore(),
    )

    orchestrator = PaperAnalysisOrchestrator(
        pdf_loader=PDFLoader(),
        chunker=DocumentChunker(chunk_size=1200, chunk_overlap=150),
        retriever=retriever,
        planner_agent=PlannerAgent(llm_client=llm_client),
        reader_agent=ReaderAgent(llm_client=llm_client),
        critic_agent=CriticAgent(llm_client=llm_client),
        writer_agent=WriterAgent(llm_client=llm_client),
    )

    paper_input = PaperInput(
        source_type="pdf",
        source_path="backend/data/raw/example.pdf",
        user_query="Analyze this paper and generate a structured reading report.",
    )

    state = orchestrator.run(paper_input)

    assert state.status == AnalysisStatus.COMPLETED
    assert state.document is not None
    assert state.document.has_chunks()
    assert state.analysis_plan is not None
    assert state.evidence_bundle is not None
    assert state.reader_notes is not None
    assert state.critic_notes is not None
    assert state.final_report is not None
    assert state.final_report.to_markdown()
    assert len(state.step_history) > 0

def test_create_default_orchestrator_with_mock_settings(tmp_path):
  settings = AppSettings(
      project_root=tmp_path,
      llm_provider="mock",
      llm_vendor="mock",
      llm_model="mock-llm",
      embedding_provider="mock",
      embedding_vendor="mock",
      embedding_model="mock-embedding",
  )

  orchestrator = create_default_orchestrator(settings)

  assert isinstance(orchestrator, PaperAnalysisOrchestrator)