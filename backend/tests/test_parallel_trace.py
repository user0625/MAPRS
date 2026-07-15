import json
import threading
import time

from backend.agents.critic_agent import CriticAgent
from backend.agents.planner_agent import PlannerAgent
from backend.agents.writer_agent import WriterAgent
from backend.core.config import AppSettings
from backend.core.orchestrator import PaperAnalysisOrchestrator
from backend.core.state import AnalysisStatus
from backend.llm.client import MockLLMClient
from backend.exporters.report_exporter import ReportExporter
from backend.schemas.agent_io import ReaderNotes
from backend.schemas.paper import PaperInput
from backend.tools.chunker import DocumentChunker
from backend.tools.embedder import MockEmbedder
from backend.tools.pdf_loader import PDFLoader
from backend.tools.retriever import PaperRetriever
from backend.tools.vector_store import NumpyVectorStore


class BoundedReader:
    def __init__(self, client, always_fail=()):
        self.llm_client = client
        self.always_fail = set(always_fail)
        self.lock = threading.Lock()
        self.active = 0
        self.maximum_active = 0
        self.attempts = {}

    @property
    def is_mock(self):
        return True

    def run(self, reader_input):
        task_id = reader_input.analysis_plan.tasks[0].task_id
        with self.lock:
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
            self.attempts[task_id] = self.attempts.get(task_id, 0) + 1
            attempt = self.attempts[task_id]
        try:
            time.sleep(0.015)
            if task_id in self.always_fail or (task_id == "task_002" and attempt == 1):
                raise TimeoutError("private upstream detail must not be persisted")
            return ReaderNotes(
                problem_statement=f"problem-{task_id}",
                main_contributions=[f"contribution-{task_id}"],
                method_summary=f"method-{task_id}",
                important_evidence_ids=[],
            )
        finally:
            with self.lock:
                self.active -= 1


def orchestrator(reader, settings):
    client = reader.llm_client
    return PaperAnalysisOrchestrator(
        pdf_loader=PDFLoader(),
        chunker=DocumentChunker(chunk_size=1200, chunk_overlap=150),
        retriever=PaperRetriever(MockEmbedder(dimension=64), NumpyVectorStore()),
        planner_agent=PlannerAgent(client), reader_agent=reader,
        critic_agent=CriticAgent(client), writer_agent=WriterAgent(client), settings=settings,
    )


def test_parallel_reader_is_bounded_retries_and_marks_partial_coverage():
    client = MockLLMClient()
    reader = BoundedReader(client, always_fail={"task_003"})
    settings = AppSettings(
        _env_file=None, parallel_reader_enabled=True, reader_parallelism=2,
        reader_branch_retries=1, verifier_enabled=False,
    )
    state = orchestrator(reader, settings).run(PaperInput(
        source_path="backend/data/raw/example.pdf",
        user_query="private complete question that must not enter the trace",
    ))
    assert state.status == AnalysisStatus.COMPLETED
    execution = state.metadata["reader_execution"]
    assert execution["mode"] == "parallel"
    assert execution["branch_count"] == 4
    assert execution["failed_branches"] == 1
    assert execution["coverage_gaps"] == ["task_003"]
    assert reader.maximum_active <= 2
    assert reader.attempts["task_002"] == 2
    assert reader.attempts["task_003"] == 2
    assert state.reader_notes.problem_statement.split("\n\n") == [
        "problem-task_001", "problem-task_002", "problem-task_004"
    ]
    assert "task_003" in (state.final_report.warning or "")


def test_trace_is_content_free_and_has_stage_cost_fields(tmp_path):
    client = MockLLMClient()
    settings = AppSettings(_env_file=None, analysis_trace_enabled=True, verifier_enabled=False)
    state = orchestrator(BoundedReader(client), settings).run(PaperInput(
        source_path="backend/data/raw/example.pdf",
        user_query="SECRET complete paper question",
    ))
    trace = state.metadata["trace"]
    serialized = json.dumps(trace)
    assert trace["schema_version"] == "analysis-trace-v1"
    assert trace["privacy"] == "content_free"
    assert "SECRET complete paper question" not in serialized
    assert "backend/data/raw/example.pdf" not in serialized
    assert "Authorization" not in serialized
    assert "api_key" not in serialized.casefold()
    assert {item["stage"] for item in trace["events"]} >= {
        "parse_pdf", "chunk_document", "plan_analysis", "retrieve_evidence",
        "read_paper", "criticize_paper", "write_report", "verify_report",
    }
    assert all("duration_ms" in item and "estimated_cost_usd" in item for item in trace["events"])
    ReportExporter().save_all(
        state, tmp_path / "report.md", tmp_path / "report.json", tmp_path / "state.json"
    )
    assert state.metadata["trace"]["events"][-1]["stage"] == "export"


def test_cancellation_is_content_free_and_stops_before_new_stage():
    client = MockLLMClient()
    settings = AppSettings(_env_file=None, analysis_trace_enabled=True)
    state = orchestrator(BoundedReader(client), settings).run(
        PaperInput(source_path="backend/data/raw/example.pdf", user_query="private"),
        cancel_check=lambda: True,
    )
    assert state.metadata["canceled"] is True
    assert [item["status"] for item in state.metadata["trace"]["events"]] == ["canceled"]
