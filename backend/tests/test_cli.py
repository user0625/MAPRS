import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import backend.app.cli as cli_module
from backend.app.cli import app
from backend.core.config import AppSettings

runner = CliRunner()


@pytest.mark.skipif(
    not Path("backend/data/raw/example.pdf").exists(),
    reason="Test PDF does not exist.",
)
@pytest.mark.parametrize(
    ("language", "expected_title"),
    [("zh", "论文阅读报告"), ("en", "Paper Reading Report")],
)
def test_cli_analyze_mock_workflow(tmp_path, monkeypatch, language, expected_title):
    output_path = tmp_path / "report.md"
    state_path = tmp_path / "state.json"

    settings = AppSettings(
        _env_file=None,
        project_root=tmp_path,
        llm_provider="mock",
        llm_vendor="mock",
        llm_model="mock-llm",
        embedding_provider="mock",
        embedding_vendor="mock",
        embedding_model="mock-embedding",
    )
    monkeypatch.setattr(cli_module, "get_settings", lambda: settings)

    result = runner.invoke(
        app,
        [
            # "analyze",
            "--pdf",
            "backend/data/raw/example.pdf",
            "--output",
            str(output_path),
            "--language",
            language,
            "--state-json",
            str(state_path),
        ],
    )
    if result.exit_code != 0:
        print("Exit code:", result.exit_code)
        print("Output:", result.output)
        print("Exception:", result.exception)
        if result.exc_info:
            import traceback
            traceback.print_tb(result.exc_info[2])
    assert result.exit_code == 0
    assert output_path.exists()
    assert state_path.exists()

    content = output_path.read_text(encoding="utf-8")
    assert content.strip()
    assert expected_title in content

    state_data = json.loads(state_path.read_text(encoding="utf-8"))
    assert state_data["metadata"]["output_language"] == language
