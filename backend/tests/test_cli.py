from pathlib import Path

import pytest
from typer.testing import CliRunner

from backend.app.cli import app

runner = CliRunner()


@pytest.mark.skipif(
    not Path("backend/data/raw/example.pdf").exists(),
    reason="Test PDF does not exist.",
)
def test_cli_analyze_mock_workflow(tmp_path):
    output_path = tmp_path / "report.md"

    result = runner.invoke(
        app,
        [
            # "analyze",
            "--pdf",
            "backend/data/raw/example.pdf",
            "--output",
            str(output_path),
            "--language",
            "zh",
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

    content = output_path.read_text(encoding="utf-8")
    assert content.strip()
    assert "论文阅读报告" in content or "Paper Reading Report" in content