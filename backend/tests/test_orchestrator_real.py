import os
from pathlib import Path

import pytest

from backend.core.config import get_settings
from backend.core.orchestrator import create_default_orchestrator
from backend.core.state import AnalysisStatus
from backend.schemas.paper import PaperInput


@pytest.mark.skipif(
    os.getenv("RUN_REAL_LLM_TESTS") != "1",
    reason="Real LLM tests are disabled.",
)
@pytest.mark.skipif(
    not Path("backend/data/raw/example.pdf").exists(),
    reason="Test PDF does not exist.",
)
def test_real_orchestrator_smoke():
    settings = get_settings()
    assert settings.use_real_llm, "Real LLM test requires LLM_PROVIDER=openai_compatible."

    orchestrator = create_default_orchestrator(settings)
    assert orchestrator.planner_agent.llm_client.provider != "mock"

    paper_input = PaperInput(
        source_type="pdf",
        source_path="backend/data/raw/example.pdf",
        user_query="Analyze this paper briefly and generate a structured report.",
    )

    state = orchestrator.run(paper_input)
    if state.status == AnalysisStatus.FAILED:
      print("Error:", state.error_message)
      print("Steps:", state.step_history)

    assert state.status == AnalysisStatus.COMPLETED
    assert state.final_report is not None
    assert state.final_report.to_markdown()
