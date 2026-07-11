import pytest

from backend.agents.base_agent import AgentError, BaseAgent
from backend.llm.client import LLMError, MockLLMClient


def test_base_agent_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        BaseAgent(name="base", llm_client=MockLLMClient())


def test_base_agent_rejects_empty_name():
    class DummyAgent(BaseAgent):
        def run(self, agent_input):
            return None

    with pytest.raises(ValueError):
        DummyAgent(name="   ", llm_client=MockLLMClient())


def test_base_agent_preserves_llm_error_detail():
    class FailingClient(MockLLMClient):
        def generate_pydantic(self, *args, **kwargs):
            raise LLMError("Schema validation failed: tasks.0.priority")

    class DummyAgent(BaseAgent):
        def run(self, agent_input):
            return self.generate_pydantic("prompt", object)

    agent = DummyAgent(name="planner_agent", llm_client=FailingClient())

    with pytest.raises(AgentError, match=r"tasks\.0\.priority"):
        agent.run(None)
