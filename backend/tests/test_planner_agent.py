from backend.agents.planner_agent import PlannerAgent
from backend.llm.client import MockLLMClient
from backend.schemas.agent_io import AnalysisPlan, PlannerInput
from backend.schemas.paper import PaperMetadata


def test_planner_agent_with_mock_llm():
    agent = PlannerAgent(
        llm_client=MockLLMClient(),
    )

    planner_input = PlannerInput(
        paper_metadata=PaperMetadata(
            paper_id="paper_001",
            title="Example Paper",
            abstract="This paper studies multi-agent paper reading.",
        ),
        user_query="Analyze this paper.",
    )

    plan = agent.run(planner_input)

    assert isinstance(plan, AnalysisPlan)
    assert len(plan.tasks) > 0
    assert len(plan.focus_questions) > 0
    assert plan.need_retrieval is True


def test_planner_agent_prompt_contains_metadata():
    agent = PlannerAgent(
        llm_client=MockLLMClient(),
    )

    planner_input = PlannerInput(
        paper_metadata=PaperMetadata(
            paper_id="paper_001",
            title="Example Paper",
            abstract="This is an abstract.",
        ),
        user_query="Analyze this paper.",
    )

    prompt = agent._build_prompt(planner_input)

    assert "Example Paper" in prompt
    assert "This is an abstract." in prompt
    assert "Analyze this paper." in prompt
    assert "JSON" in prompt or "schema" in prompt