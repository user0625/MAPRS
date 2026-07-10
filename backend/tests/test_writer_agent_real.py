import os

import pytest

from backend.agents.writer_agent import WriterAgent
from backend.core.config import get_settings
from backend.llm.client import create_llm_client
from backend.schemas.agent_io import (
    AgentRole,
    AgentTask,
    AnalysisPlan,
    CriticNotes,
    ReaderNotes,
    WriterInput,
)
from backend.schemas.paper import PaperMetadata
from backend.schemas.report import FinalReport


@pytest.mark.skipif(
    os.getenv("RUN_REAL_LLM_TESTS") != "1",
    reason="Real LLM tests are disabled.",
)
def test_real_writer_agent_smoke():
    settings = get_settings()
    assert settings.use_real_llm, "Real LLM test requires LLM_PROVIDER=openai_compatible."

    llm_client = create_llm_client(settings)
    assert llm_client.provider != "mock"

    agent = WriterAgent(llm_client=llm_client)

    plan = AnalysisPlan(
        tasks=[
            AgentTask(
                task_id="task_001",
                name="write_report",
                description="Generate final paper reading report.",
                assigned_to=AgentRole.WRITER,
            )
        ],
        focus_questions=["What is the method?", "What are the limitations?"],
        required_sections=["Abstract", "Method", "Experiments"],
    )

    reader_notes = ReaderNotes(
        problem_statement="The paper proposes the Transformer for sequence transduction.",
        background="Previous models rely on recurrence or convolution.",
        main_contributions=[
            "The paper introduces an attention-only architecture.",
            "The paper demonstrates strong results on machine translation tasks.",
        ],
        method_summary="The method uses self-attention, multi-head attention, and positional encoding.",
        experiment_summary="The method is evaluated on WMT translation benchmarks.",
        conclusion_summary="The paper concludes that attention-only architectures are effective.",
        key_terms=["Transformer", "self-attention", "machine translation"],
        important_evidence_ids=["ev_q001_r001"],
    )

    critic_notes = CriticNotes(
        strengths=[
            "The architecture is simple and influential.",
            "The experiments demonstrate strong empirical performance.",
        ],
        limitations=[
            "The initial evaluation focuses mainly on machine translation.",
        ],
        missing_experiments=[
            "More analysis on long-context behavior could be useful.",
        ],
        potential_weaknesses=[
            "The model may require substantial data and compute.",
        ],
        novelty_assessment="Highly novel and influential.",
        reliability_assessment="The results are reliable within the reported experimental setting.",
        reproducibility_notes=[
            "Implementation details and hyperparameters are important for reproduction.",
        ],
        evidence_ids=["ev_q001_r001"],
    )

    writer_input = WriterInput(
        paper_metadata=PaperMetadata(
            paper_id="paper_001",
            title="Attention Is All You Need",
            year=2017,
        ),
        analysis_plan=plan,
        reader_notes=reader_notes,
        critic_notes=critic_notes,
        evidence_bundle=None,
        output_language="zh",
    )

    report = agent.run(writer_input)

    assert isinstance(report, FinalReport)
    assert report.to_markdown()
    assert report.sections or report.markdown_content
