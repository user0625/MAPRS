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
    orchestrator = create_default_orchestrator(settings)

    paper_input = PaperInput(
        source_type="pdf",
        source_path="backend/data/raw/example.pdf",
        user_query="Analyze this paper briefly and generate a structured report.",
    )

    state = orchestrator.run(paper_input)
    if state.status == AnalysisStatus.FAILED:
      print("Error:", state.error_message)   # 如果有该属性
      print("Error detail:", getattr(state, 'error', 'No error field'))

    assert state.status == AnalysisStatus.COMPLETED
    assert state.final_report is not None
    assert state.final_report.to_markdown()