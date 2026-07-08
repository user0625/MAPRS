from backend.schemas.agent_io import (
    AgentRole,
    AgentTask,
    AnalysisPlan,
    CriticInput,
    CriticNotes,
    EvidenceBundle,
    EvidenceItem,
    PlannerInput,
    ReaderInput,
    ReaderNotes,
    WriterInput,
)
from backend.schemas.paper import PaperChunk, PaperMetadata


def test_analysis_plan_creation():
    task = AgentTask(
        task_id="task_001",
        name="extract_contributions",
        description="Extract the main contributions of the paper.",
        assigned_to=AgentRole.READER,
        priority=1,
    )

    plan = AnalysisPlan(
        tasks=[task],
        focus_questions=["What are the main contributions?"],
        required_sections=["Abstract", "Introduction", "Method"],
    )

    assert plan.mode == "single_paper_analysis"
    assert len(plan.tasks) == 1
    assert plan.tasks[0].assigned_to == "reader"


def test_evidence_item_from_chunk():
    chunk = PaperChunk(
        chunk_id="page_1_chunk_1",
        paper_id="paper_001",
        text="This paper proposes a new multi-agent paper reading system.",
        page_start=1,
        page_end=1,
        section="Abstract",
    )

    evidence = EvidenceItem.from_chunk(
        chunk=chunk,
        evidence_id="ev_001",
        query="main contribution",
        score=0.95,
    )

    assert evidence.evidence_id == "ev_001"
    assert evidence.chunk_id == "page_1_chunk_1"
    assert evidence.score == 0.95
    assert evidence.page_start == 1


def test_reader_input_creation():
    metadata = PaperMetadata(
        paper_id="paper_001",
        title="Example Paper",
        total_pages=1,
    )

    chunk = PaperChunk(
        chunk_id="page_1_chunk_1",
        paper_id="paper_001",
        text="This is a test chunk.",
        page_start=1,
        page_end=1,
    )

    plan = AnalysisPlan(
        tasks=[
            AgentTask(
                task_id="task_001",
                name="read_paper",
                assigned_to=AgentRole.READER,
            )
        ],
        focus_questions=["What problem does the paper solve?"],
    )

    reader_input = ReaderInput(
        paper_metadata=metadata,
        chunks=[chunk],
        analysis_plan=plan,
    )

    assert reader_input.paper_metadata.title == "Example Paper"
    assert len(reader_input.chunks) == 1


def test_writer_input_creation():
    metadata = PaperMetadata(
        paper_id="paper_001",
        title="Example Paper",
    )

    plan = AnalysisPlan(
        tasks=[
            AgentTask(
                task_id="task_001",
                name="extract_method",
                assigned_to=AgentRole.READER,
            )
        ],
        focus_questions=["What is the method?"],
    )

    reader_notes = ReaderNotes(
        problem_statement="The paper studies scientific paper understanding.",
        main_contributions=["A multi-agent reading workflow."],
        method_summary="The system uses multiple specialized agents.",
        experiment_summary="Experiments are summarized from the paper.",
        conclusion_summary="The system improves reading efficiency.",
    )

    critic_notes = CriticNotes(
        strengths=["Clear modular design."],
        limitations=["Evaluation may be limited."],
        novelty_assessment="Moderately novel as an engineering system.",
        reliability_assessment="Reliability depends on LLM output quality.",
    )

    evidence_bundle = EvidenceBundle(
        query_list=["method", "experiments"],
        items=[],
    )

    writer_input = WriterInput(
        paper_metadata=metadata,
        analysis_plan=plan,
        reader_notes=reader_notes,
        critic_notes=critic_notes,
        evidence_bundle=evidence_bundle,
        output_language="zh",
    )

    assert writer_input.output_language == "zh"
    assert writer_input.reader_notes.main_contributions[0] == "A multi-agent reading workflow."