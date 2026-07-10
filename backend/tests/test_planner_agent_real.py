import os

import pytest

from backend.agents.planner_agent import PlannerAgent
from backend.core.config import get_settings
from backend.llm.client import create_llm_client
from backend.schemas.agent_io import AnalysisPlan, PlannerInput
from backend.schemas.paper import PaperMetadata


@pytest.mark.skipif(
    os.getenv("RUN_REAL_LLM_TESTS") != "1",
    reason="Real LLM tests are disabled.",
)
def test_real_planner_agent_smoke():
    settings = get_settings()
    assert settings.use_real_llm, "Real LLM test requires LLM_PROVIDER=openai_compatible."

    llm_client = create_llm_client(settings)
    assert llm_client.provider != "mock"

    agent = PlannerAgent(llm_client=llm_client)

    planner_input = PlannerInput(
        paper_metadata=PaperMetadata(
            paper_id="paper_001",
            title="Attention Is All You Need",
            abstract=(
                "The dominant sequence transduction models are based on complex "
                "recurrent or convolutional neural networks. This paper proposes "
                "the Transformer, a model architecture based solely on attention mechanisms."
            ),
            year=2017,
        ),
        user_query="Generate a structured reading plan.",
    )

    plan = agent.run(planner_input)
    print(plan)
    assert isinstance(plan, AnalysisPlan)
    assert len(plan.tasks) > 0
    assert len(plan.focus_questions) > 0
