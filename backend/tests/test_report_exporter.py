import json

import pytest

from backend.core.state import AnalysisState
from backend.exporters.report_exporter import ReportExporter, ReportExportError
from backend.schemas.paper import PaperInput
from backend.schemas.report import FinalReport, ReportSection


def make_report() -> FinalReport:
    return FinalReport(
        title="Paper Reading Report",
        paper_title="Example Paper",
        sections=[
            ReportSection(
                title="TL;DR",
                content="This is a short summary.",
                order=1,
            ),
            ReportSection(
                title="Method Summary",
                content="This is the method summary.",
                order=2,
            ),
        ],
    )


def test_save_markdown(tmp_path):
    exporter = ReportExporter()
    report = make_report()

    output_path = tmp_path / "report.md"
    saved_path = exporter.save_markdown(report, output_path)

    assert saved_path == output_path
    assert output_path.exists()

    content = output_path.read_text(encoding="utf-8")
    assert "# Paper Reading Report" in content
    assert "## TL;DR" in content
    assert "This is a short summary." in content


def test_save_report_json(tmp_path):
    exporter = ReportExporter()
    report = make_report()

    output_path = tmp_path / "report.json"
    saved_path = exporter.save_report_json(report, output_path)

    assert saved_path == output_path
    assert output_path.exists()

    data = json.loads(output_path.read_text(encoding="utf-8"))
    assert data["title"] == "Paper Reading Report"
    assert data["paper_title"] == "Example Paper"
    assert len(data["sections"]) == 2


def test_save_state_json(tmp_path):
    exporter = ReportExporter()

    state = AnalysisState(
        task_id="task_001",
        paper_input=PaperInput(
            source_type="pdf",
            source_path="backend/data/raw/example.pdf",
        ),
    )
    state.final_report = make_report()

    output_path = tmp_path / "state.json"
    saved_path = exporter.save_state_json(state, output_path)

    assert saved_path == output_path
    assert output_path.exists()

    data = json.loads(output_path.read_text(encoding="utf-8"))
    assert data["task_id"] == "task_001"
    assert data["final_report"]["title"] == "Paper Reading Report"


def test_save_all(tmp_path):
    exporter = ReportExporter()

    state = AnalysisState(
        task_id="task_001",
        paper_input=PaperInput(
            source_type="pdf",
            source_path="backend/data/raw/example.pdf",
        ),
    )
    state.final_report = make_report()

    saved_paths = exporter.save_all(
        state=state,
        report_md_path=tmp_path / "report.md",
        report_json_path=tmp_path / "report.json",
        state_json_path=tmp_path / "state.json",
    )

    assert saved_paths["markdown"].exists()
    assert saved_paths["report_json"].exists()
    assert saved_paths["state_json"].exists()


def test_save_all_rejects_state_without_report(tmp_path):
    exporter = ReportExporter()

    state = AnalysisState(
        task_id="task_001",
        paper_input=PaperInput(
            source_type="pdf",
            source_path="backend/data/raw/example.pdf",
        ),
    )

    with pytest.raises(ReportExportError):
        exporter.save_all(
            state=state,
            report_md_path=tmp_path / "report.md",
        )