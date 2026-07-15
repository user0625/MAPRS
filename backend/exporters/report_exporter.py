from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from backend.core.state import AnalysisState
from backend.schemas.report import FinalReport
from backend.core.telemetry import TraceEvent, append_state_event


class ReportExportError(Exception):
  """Raised when report exporting fails."""


class ReportExporter:
  """
    Export FinalReport and AnalysisState to files.

    This class only handles result saving. It does not run analysis workflow.
  """

  def save_markdown(self, report: FinalReport, output_path: str | Path,) -> Path:
    """
      Save FinalReport as Markdown.
    """

    path = Path(output_path)

    try:
      path.parent.mkdir(parents=True, exist_ok=True)
      path.write_text(report.to_markdown(), encoding="utf-8")
    except Exception as exc:
      raise ReportExportError(f"Failed to save Markdown report: {path}") from exc

    return path

  def save_report_json(self, report: FinalReport, output_path: str | Path,) -> Path:
    """
      Save FinalReport as JSON.
    """

    path = Path(output_path)

    try:
      path.parent.mkdir(parents=True, exist_ok=True)
      data = report.model_dump(mode="json")
      path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
      )
    except Exception as exc:
      raise ReportExportError(f"Failed to save report JSON: {path}") from exc

    return path

  def save_state_json(self, state: AnalysisState, output_path: str | Path,) -> Path:
    """
      Save full AnalysisState as JSON.

      Useful for debugging, reproducibility, and future UI display.
    """

    path = Path(output_path)

    try:
      path.parent.mkdir(parents=True, exist_ok=True)
      data = state.model_dump(mode="json")
      path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
      )
    except Exception as exc:
      raise ReportExportError(f"Failed to save analysis state JSON: {path}") from exc

    return path

  def save_all(
    self,
    state: AnalysisState,
    report_md_path: str | Path,
    report_json_path: str | Path | None = None,
    state_json_path: str | Path | None = None,
) -> dict[str, Path]:
    """
      Save Markdown report and optional JSON artifacts.

      Returns a dictionary of saved artifact paths.
    """

    if state.final_report is None:
      raise ReportExportError("Cannot export because state.final_report is None.")

    saved_paths: dict[str, Path] = {}
    started = time.perf_counter()
    started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    saved_paths["markdown"] = self.save_markdown(
      report=state.final_report,
      output_path=report_md_path,
    )

    if report_json_path is not None:
      saved_paths["report_json"] = self.save_report_json(
        report=state.final_report,
        output_path=report_json_path,
      )

    if state.metadata.get("trace") and not any(
      item.get("stage") == "export" for item in state.metadata["trace"].get("events", [])
      if isinstance(item, dict)
    ):
      append_state_event(state, TraceEvent(
        stage="export", status="success", started_at=started_at,
        duration_ms=(time.perf_counter() - started) * 1000,
        evidence_count=len(state.evidence_bundle.items) if state.evidence_bundle else 0,
      ))

    if state_json_path is not None:
      saved_paths["state_json"] = self.save_state_json(
        state=state,
        output_path=state_json_path,
      )

    return saved_paths
