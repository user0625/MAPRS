from backend.core.state import AnalysisState, AnalysisStatus, StepStatus
from backend.schemas.paper import PaperInput


def test_create_analysis_state():
    paper_input = PaperInput(
        source_type="pdf",
        source_path="data/raw/example.pdf",
    )

    state = AnalysisState(
        task_id="task_001",
        paper_input=paper_input,
    )

    assert state.task_id == "task_001"
    assert state.status == AnalysisStatus.CREATED
    assert state.has_document() is False
    assert state.has_report() is False


def test_update_status():
    paper_input = PaperInput(
        source_type="pdf",
        source_path="data/raw/example.pdf",
    )

    state = AnalysisState(
        task_id="task_001",
        paper_input=paper_input,
    )

    state.update_status(AnalysisStatus.PARSING)

    assert state.status == AnalysisStatus.PARSING


def test_add_step_record():
    paper_input = PaperInput(
        source_type="pdf",
        source_path="data/raw/example.pdf",
    )

    state = AnalysisState(
        task_id="task_001",
        paper_input=paper_input,
    )

    state.add_step(
        step_name="parse_pdf",
        status=StepStatus.SUCCESS,
        message="PDF parsed successfully.",
        metadata={"pages": 10},
    )

    assert len(state.step_history) == 1
    assert state.step_history[0].step_name == "parse_pdf"
    assert state.step_history[0].metadata["pages"] == 10


def test_mark_failed():
    paper_input = PaperInput(
        source_type="pdf",
        source_path="data/raw/example.pdf",
    )

    state = AnalysisState(
        task_id="task_001",
        paper_input=paper_input,
    )

    state.mark_failed("PDF file not found.")

    assert state.is_failed() is True
    assert state.error_message == "PDF file not found."
    assert len(state.step_history) == 1
    assert state.step_history[0].status == StepStatus.FAILED


def test_mark_completed():
    paper_input = PaperInput(
        source_type="pdf",
        source_path="data/raw/example.pdf",
    )

    state = AnalysisState(
        task_id="task_001",
        paper_input=paper_input,
    )

    state.mark_completed()

    assert state.is_completed() is True
    assert state.completed_at is not None