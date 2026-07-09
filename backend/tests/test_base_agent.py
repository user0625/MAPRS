import pytest

from backend.agents.base_agent import BaseAgent
from backend.llm.client import MockLLMClient


def test_base_agent_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        BaseAgent(name="base", llm_client=MockLLMClient())


def test_base_agent_rejects_empty_name():
    class DummyAgent(BaseAgent):
        def run(self, agent_input):
            return None

    with pytest.raises(ValueError):
        DummyAgent(name="   ", llm_client=MockLLMClient())