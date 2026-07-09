from backend.agents.reader_agent import ReaderAgent
from backend.llm.client import MockLLMClient
from backend.schemas.agent_io import (
    AgentRole,
    AgentTask,
    AnalysisPlan,
    EvidenceBundle,
    EvidenceItem,
    ReaderInput,
    ReaderNotes,
)
from backend.schemas.paper import PaperChunk, PaperMetadata


def make_plan() -> AnalysisPlan:
    return AnalysisPlan(
        tasks=[
            AgentTask(
                task_id="task_001",
                name="extract_problem_statement",
                description="Extract the research problem.",
                assigned_to=AgentRole.READER,
            ),
            AgentTask(
                task_id="task_002",
                name="summarize_method",
                description="Summarize the proposed method.",
                assigned_to=AgentRole.READER,
            ),
        ],
        focus_questions=[
            "What problem does the paper solve?",
            "What is the proposed method?",
        ],
        required_sections=["Abstract", "Introduction", "Method"],
        need_retrieval=True,
    )


def make_chunks() -> list[PaperChunk]:
    return [
        PaperChunk(
            chunk_id="paper_001_page_1_chunk_1",
            paper_id="paper_001",
            text="This paper studies automatic scientific paper reading.",
            page_start=1,
            page_end=1,
            section="Abstract",
        ),
        PaperChunk(
            chunk_id="paper_001_page_2_chunk_1",
            paper_id="paper_001",
            text="The proposed method uses Planner, Reader, Critic, and Writer agents.",
            page_start=2,
            page_end=2,
            section="Method",
        ),
    ]


def test_reader_agent_with_mock_llm_and_chunks():
    agent = ReaderAgent(llm_client=MockLLMClient())

    reader_input = ReaderInput(
        paper_metadata=PaperMetadata(
            paper_id="paper_001",
            title="Example Paper",
            abstract="This paper studies multi-agent paper reading.",
        ),
        chunks=make_chunks(),
        analysis_plan=make_plan(),
    )

    notes = agent.run(reader_input)

    assert isinstance(notes, ReaderNotes)
    assert notes.problem_statement
    assert len(notes.main_contributions) > 0
    assert notes.method_summary
    assert notes.experiment_summary


def test_reader_agent_with_evidence_bundle():
    agent = ReaderAgent(llm_client=MockLLMClient())

    evidence_bundle = EvidenceBundle(
        query_list=["What is the method?"],
        items=[
            EvidenceItem(
                evidence_id="ev_q001_r001",
                query="What is the method?",
                chunk_id="paper_001_page_2_chunk_1",
                paper_id="paper_001",
                text="The method uses multiple specialized agents.",
                score=0.9,
                page_start=2,
                page_end=2,
                section="Method",
            )
        ],
    )

    reader_input = ReaderInput(
        paper_metadata=PaperMetadata(
            paper_id="paper_001",
            title="Example Paper",
        ),
        chunks=[],
        analysis_plan=make_plan(),
        evidence_bundle=evidence_bundle,
    )

    notes = agent.run(reader_input)

    assert isinstance(notes, ReaderNotes)
    assert "ev_q001_r001" in notes.important_evidence_ids


def test_reader_agent_prompt_contains_context():
    agent = ReaderAgent(llm_client=MockLLMClient())

    reader_input = ReaderInput(
        paper_metadata=PaperMetadata(
            paper_id="paper_001",
            title="Example Paper",
            abstract="This is an abstract.",
        ),
        chunks=make_chunks(),
        analysis_plan=make_plan(),
    )

    prompt = agent._build_prompt(reader_input)

    assert "Example Paper" in prompt
    assert "This is an abstract." in prompt
    assert "automatic scientific paper reading" in prompt
    assert "Planner, Reader, Critic, and Writer" in prompt
    assert "JSON" in prompt or "schema" in prompt


def test_reader_agent_uses_evidence_context_when_available():
    agent = ReaderAgent(llm_client=MockLLMClient())

    evidence_bundle = EvidenceBundle(
        query_list=["What is the method?"],
        items=[
            EvidenceItem(
                evidence_id="ev_q001_r001",
                query="What is the method?",
                chunk_id="chunk_method",
                text="Evidence context should be used.",
                score=0.95,
                page_start=3,
                page_end=3,
                section="Method",
            )
        ],
    )

    reader_input = ReaderInput(
        paper_metadata=PaperMetadata(
            paper_id="paper_001",
            title="Example Paper",
        ),
        chunks=make_chunks(),
        analysis_plan=make_plan(),
        evidence_bundle=evidence_bundle,
    )

    context = agent._build_context(reader_input)

    assert "Evidence ID: ev_q001_r001" in context
    assert "Evidence context should be used." in context