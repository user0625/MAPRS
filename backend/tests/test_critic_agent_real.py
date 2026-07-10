import os

import pytest

from backend.agents.critic_agent import CriticAgent
from backend.core.config import get_settings
from backend.llm.client import create_llm_client
from backend.schemas.agent_io import CriticInput, CriticNotes, EvidenceBundle, EvidenceItem, ReaderNotes
from backend.schemas.paper import PaperMetadata


@pytest.mark.skipif(
    os.getenv("RUN_REAL_LLM_TESTS") != "1",
    reason="Real LLM tests are disabled.",
)
def test_real_critic_agent_smoke():
    settings = get_settings()
    assert settings.use_real_llm, "Real LLM test requires LLM_PROVIDER=openai_compatible."

    llm_client = create_llm_client(settings)
    assert llm_client.provider != "mock"

    agent = CriticAgent(llm_client=llm_client)

    reader_notes = ReaderNotes(
        problem_statement="The paper proposes the Transformer for sequence transduction.",
        background="Prior models rely heavily on recurrence or convolution.",
        main_contributions=[
            "The paper introduces a model based solely on attention mechanisms.",
            "The paper demonstrates strong results on machine translation tasks.",
        ],
        method_summary="The method uses self-attention, multi-head attention, and positional encoding.",
        experiment_summary="The model is evaluated on WMT translation benchmarks.",
        conclusion_summary="The paper concludes that attention-only architectures are effective.",
        key_terms=["Transformer", "self-attention", "machine translation"],
        important_evidence_ids=["ev_q001_r001"],
    )

    evidence_bundle = EvidenceBundle(
        query_list=["What is the method?"],
        items=[
            EvidenceItem(
                evidence_id="ev_q001_r001",
                query="What is the method?",
                chunk_id="chunk_001",
                text=(
                    "The Transformer is based entirely on attention mechanisms, "
                    "dispensing with recurrence and convolutions."
                ),
                score=0.95,
                page_start=1,
                page_end=1,
                section="Abstract",
            )
        ],
    )

    critic_input = CriticInput(
        paper_metadata=PaperMetadata(
            paper_id="paper_001",
            title="Attention Is All You Need",
            year=2017,
        ),
        reader_notes=reader_notes,
        evidence_bundle=evidence_bundle,
    )

    notes = agent.run(critic_input)

    assert isinstance(notes, CriticNotes)
    assert notes.strengths
    assert notes.limitations
    assert notes.novelty_assessment
