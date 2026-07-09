from backend.agents.writer_agent import WriterAgent
from backend.llm.client import MockLLMClient
from backend.schemas.agent_io import (
    AgentRole,
    AgentTask,
    AnalysisPlan,
    CriticNotes,
    EvidenceBundle,
    EvidenceItem,
    ReaderNotes,
    WriterInput,
)
from backend.schemas.paper import PaperMetadata
from backend.schemas.report import FinalReport


def make_plan() -> AnalysisPlan:
    return AnalysisPlan(
        tasks=[
            AgentTask(
                task_id="task_001",
                name="extract_main_contributions",
                description="Extract main contributions.",
                assigned_to=AgentRole.READER,
            ),
            AgentTask(
                task_id="task_002",
                name="criticize_limitations",
                description="Analyze limitations.",
                assigned_to=AgentRole.CRITIC,
            ),
        ],
        focus_questions=[
            "What are the main contributions?",
            "What are the limitations?",
        ],
        required_sections=["Abstract", "Method", "Experiments"],
        need_retrieval=True,
    )


def make_reader_notes() -> ReaderNotes:
    return ReaderNotes(
        problem_statement="The paper studies multi-agent scientific paper reading.",
        background="Existing tools often provide summaries but lack structured critique.",
        main_contributions=[
            "The paper proposes a multi-agent paper reading workflow.",
            "The paper separates reading, criticism, and writing into different agents.",
        ],
        method_summary="The method uses Planner, Reader, Critic, and Writer agents.",
        experiment_summary="The system is evaluated on paper understanding tasks.",
        conclusion_summary="The paper concludes that multi-agent workflows can improve paper analysis.",
        key_terms=["multi-agent", "paper reading", "RAG"],
        important_evidence_ids=["ev_q001_r001"],
    )


def make_critic_notes() -> CriticNotes:
    return CriticNotes(
        strengths=[
            "The system has a clear modular design.",
            "The workflow separates factual reading from critical analysis.",
        ],
        limitations=[
            "The evaluation may be limited in scale.",
        ],
        missing_experiments=[
            "More ablation studies are needed.",
        ],
        potential_weaknesses=[
            "The system may depend heavily on LLM output quality.",
        ],
        novelty_assessment="The system is moderately novel as an engineering workflow.",
        reliability_assessment="Reliability depends on retrieval quality and LLM consistency.",
        reproducibility_notes=[
            "The paper should provide prompts, model settings, and evaluation details.",
        ],
        evidence_ids=["ev_q001_r001"],
    )


def make_evidence_bundle() -> EvidenceBundle:
    return EvidenceBundle(
        query_list=["What are the contributions?"],
        items=[
            EvidenceItem(
                evidence_id="ev_q001_r001",
                query="What are the contributions?",
                chunk_id="chunk_001",
                paper_id="paper_001",
                text="The paper proposes a multi-agent workflow for paper reading.",
                score=0.95,
                page_start=1,
                page_end=1,
                section="Abstract",
            )
        ],
    )


def test_writer_agent_with_mock_llm_zh():
    agent = WriterAgent(llm_client=MockLLMClient())

    writer_input = WriterInput(
        paper_metadata=PaperMetadata(
            paper_id="paper_001",
            title="Example Paper",
        ),
        analysis_plan=make_plan(),
        reader_notes=make_reader_notes(),
        critic_notes=make_critic_notes(),
        evidence_bundle=make_evidence_bundle(),
        output_language="zh",
    )

    report = agent.run(writer_input)

    assert isinstance(report, FinalReport)
    assert report.title == "论文阅读报告"
    assert report.paper_title == "Example Paper"
    assert len(report.sections) > 0
    assert report.markdown_content is not None
    assert "论文阅读报告" in report.to_markdown()
    assert "主要贡献" in report.to_markdown()


def test_writer_agent_with_mock_llm_en():
    agent = WriterAgent(llm_client=MockLLMClient())

    writer_input = WriterInput(
        paper_metadata=PaperMetadata(
            paper_id="paper_001",
            title="Example Paper",
        ),
        analysis_plan=make_plan(),
        reader_notes=make_reader_notes(),
        critic_notes=make_critic_notes(),
        evidence_bundle=make_evidence_bundle(),
        output_language="en",
    )

    report = agent.run(writer_input)

    assert isinstance(report, FinalReport)
    assert report.title == "Paper Reading Report"
    assert len(report.sections) > 0
    assert "Main Contributions" in report.to_markdown()


def test_writer_agent_prompt_contains_all_inputs():
    agent = WriterAgent(llm_client=MockLLMClient())

    writer_input = WriterInput(
        paper_metadata=PaperMetadata(
            paper_id="paper_001",
            title="Example Paper",
            abstract="This is an abstract.",
        ),
        analysis_plan=make_plan(),
        reader_notes=make_reader_notes(),
        critic_notes=make_critic_notes(),
        evidence_bundle=make_evidence_bundle(),
        output_language="zh",
    )

    prompt = agent._build_prompt(writer_input)

    assert "Example Paper" in prompt
    assert "This is an abstract." in prompt
    assert "multi-agent paper reading workflow" in prompt
    assert "evaluation may be limited" in prompt
    assert "ev_q001_r001" in prompt
    assert "FinalReport" in prompt or "schema" in prompt


def test_writer_agent_without_evidence_bundle():
    agent = WriterAgent(llm_client=MockLLMClient())

    writer_input = WriterInput(
        paper_metadata=PaperMetadata(
            paper_id="paper_001",
            title="Example Paper",
        ),
        analysis_plan=make_plan(),
        reader_notes=make_reader_notes(),
        critic_notes=make_critic_notes(),
        evidence_bundle=None,
        output_language="zh",
    )

    report = agent.run(writer_input)

    assert isinstance(report, FinalReport)
    assert len(report.sections) > 0
    assert report.to_markdown()