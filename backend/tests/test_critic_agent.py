from backend.agents.critic_agent import CriticAgent
from backend.llm.client import MockLLMClient
from backend.schemas.agent_io import (
    CriticInput,
    CriticNotes,
    EvidenceBundle,
    EvidenceItem,
    ReaderNotes,
)
from backend.schemas.paper import PaperMetadata


def make_reader_notes() -> ReaderNotes:
    return ReaderNotes(
        problem_statement="The paper studies automatic scientific paper reading.",
        background="Existing tools mainly summarize papers but lack structured critique.",
        main_contributions=[
            "The paper proposes a multi-agent paper reading workflow.",
            "The paper separates reading, criticism, and writing into different agents.",
        ],
        method_summary=(
            "The method uses Planner, Reader, Critic, and Writer agents "
            "to generate structured paper reports."
        ),
        experiment_summary=(
            "The paper evaluates whether the system can produce useful reading reports."
        ),
        conclusion_summary=(
            "The paper concludes that multi-agent workflows can improve paper analysis."
        ),
        key_terms=["multi-agent", "paper reading", "RAG"],
        important_evidence_ids=["ev_q001_r001"],
    )


def make_evidence_bundle() -> EvidenceBundle:
    return EvidenceBundle(
        query_list=["What are the limitations?"],
        items=[
            EvidenceItem(
                evidence_id="ev_q001_r001",
                query="What are the limitations?",
                chunk_id="paper_001_page_3_chunk_1",
                paper_id="paper_001",
                text=(
                    "The system is evaluated on a small number of papers, "
                    "and broader evaluation is left for future work."
                ),
                score=0.91,
                page_start=3,
                page_end=3,
                section="Experiments",
            )
        ],
    )


def test_critic_agent_with_mock_llm():
    agent = CriticAgent(llm_client=MockLLMClient())

    critic_input = CriticInput(
        paper_metadata=PaperMetadata(
            paper_id="paper_001",
            title="Example Paper",
            abstract="This paper studies multi-agent paper reading.",
        ),
        reader_notes=make_reader_notes(),
        evidence_bundle=make_evidence_bundle(),
    )

    notes = agent.run(critic_input)

    assert isinstance(notes, CriticNotes)
    assert len(notes.strengths) > 0
    assert len(notes.limitations) > 0
    assert len(notes.missing_experiments) > 0
    assert notes.novelty_assessment
    assert notes.reliability_assessment
    assert "ev_q001_r001" in notes.evidence_ids


def test_critic_agent_prompt_contains_reader_notes_and_evidence():
    agent = CriticAgent(llm_client=MockLLMClient())

    critic_input = CriticInput(
        paper_metadata=PaperMetadata(
            paper_id="paper_001",
            title="Example Paper",
        ),
        reader_notes=make_reader_notes(),
        evidence_bundle=make_evidence_bundle(),
    )

    prompt = agent._build_prompt(critic_input)

    assert "Example Paper" in prompt
    assert "automatic scientific paper reading" in prompt
    assert "multi-agent paper reading workflow" in prompt
    assert "ev_q001_r001" in prompt
    assert "small number of papers" in prompt
    assert "JSON" in prompt or "schema" in prompt


def test_critic_agent_without_evidence_bundle():
    agent = CriticAgent(llm_client=MockLLMClient())

    critic_input = CriticInput(
        paper_metadata=PaperMetadata(
            paper_id="paper_001",
            title="Example Paper",
        ),
        reader_notes=make_reader_notes(),
        evidence_bundle=None,
    )

    notes = agent.run(critic_input)

    assert isinstance(notes, CriticNotes)
    assert len(notes.strengths) > 0
    assert len(notes.limitations) > 0


def test_critic_agent_formats_no_evidence_context():
    agent = CriticAgent(llm_client=MockLLMClient())

    context = agent._format_evidence_context(None)

    assert context == "No retrieved evidence provided."