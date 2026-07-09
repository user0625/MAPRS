import os

import pytest

from backend.agents.reader_agent import ReaderAgent
from backend.core.config import get_settings
from backend.llm.client import create_llm_client
from backend.schemas.agent_io import AgentRole, AgentTask, AnalysisPlan, ReaderInput, ReaderNotes
from backend.schemas.paper import PaperChunk, PaperMetadata


@pytest.mark.skipif(
    os.getenv("RUN_REAL_LLM_TESTS") != "1",
    reason="Real LLM tests are disabled.",
)
def test_real_reader_agent_smoke():
    settings = get_settings()
    llm_client = create_llm_client(settings)

    agent = ReaderAgent(llm_client=llm_client)

    plan = AnalysisPlan(
        tasks=[
            AgentTask(
                task_id="task_001",
                name="summarize_method",
                description="Summarize the proposed method.",
                assigned_to=AgentRole.READER,
            )
        ],
        focus_questions=["What is the proposed method?"],
        required_sections=["Abstract", "Method"],
    )

    chunks = [
        PaperChunk(
            chunk_id="chunk_001",
            paper_id="paper_001",
            text=(
                "This paper proposes the Transformer, a model architecture "
                "based solely on attention mechanisms, dispensing with recurrence and convolutions."
            ),
            page_start=1,
            page_end=1,
            section="Abstract",
        )
    ]

    reader_input = ReaderInput(
        paper_metadata=PaperMetadata(
            paper_id="paper_001",
            title="Attention Is All You Need",
            year=2017,
        ),
        chunks=chunks,
        analysis_plan=plan,
    )

    notes = agent.run(reader_input)

    assert isinstance(notes, ReaderNotes)
    assert notes.method_summary
    assert notes.problem_statement or notes.background