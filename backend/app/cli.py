from __future__ import annotations

from pathlib import Path
from typing import Literal

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from backend.core.config import get_settings
from backend.core.orchestrator import create_default_orchestrator
from backend.core.state import AnalysisStatus
from backend.schemas.paper import PaperInput

app = typer.Typer(
  name="paper-agent",
  help="Multi-Agent Paper Reader System CLI",
)

console = Console()


@app.command()
def analyze(
  pdf: Path = typer.Option(
    ...,
    "--pdf",
    "-p",
    help="Path to the input PDF file.",
  ),
  output: Path = typer.Option(
    Path("backend/outputs/reports/report.md"),
    "--output",
    "-o",
    help="Path to save the generated Markdown report.",
  ),
  query: str = typer.Option(
    "Analyze this paper and generate a structured reading report.",
    "--query",
    "-q",
    help="User query or analysis instruction.",
  ),
  language: Literal["zh", "en"] = typer.Option(
    "zh",
    "--language",
    "-l",
    help="Output language: zh or en.",
  ),
  verbose: bool = typer.Option(
    False,
    "--verbose",
    "-v",
    help="Print detailed workflow steps.",
  ),) -> None:
  """
    Analyze a paper PDF and generate a Markdown reading report.
  """

  console.print(
    Panel.fit(
      "[bold cyan]Multi-Agent Paper Reader System[/bold cyan]\n"
      "Starting paper analysis...",
      border_style="cyan",
    )
  )

  if not pdf.exists():
    console.print(f"[bold red]Error:[/bold red] PDF file does not exist: {pdf}")
    raise typer.Exit(code=1)

  if pdf.suffix.lower() != ".pdf":
    console.print(f"[bold red]Error:[/bold red] Expected a .pdf file, got: {pdf}")
    raise typer.Exit(code=1)

  settings = get_settings()
  orchestrator = create_default_orchestrator(settings)

  paper_input = PaperInput(
    source_type="pdf",
    source_path=str(pdf),
    user_query=query,
  )

  state = orchestrator.run(paper_input=paper_input, output_language=language)

  if verbose:
    _print_step_history(state)

  if state.status != AnalysisStatus.COMPLETED:
    console.print("[bold red]Analysis failed.[/bold red]")
    console.print(f"[red]{state.error_message or 'Unknown error.'}[/red]")
    raise typer.Exit(code=1)

  if state.final_report is None:
    console.print("[bold red]Analysis completed but no final report was generated.[/bold red]")
    raise typer.Exit(code=1)

  # If your WriterAgent currently hardcodes output_language="zh" inside orchestrator,
  # language will not take effect yet. See section 6 below for how to pass it through.
  markdown = state.final_report.to_markdown()

  output.parent.mkdir(parents=True, exist_ok=True)
  output.write_text(markdown, encoding="utf-8")

  console.print(
    Panel.fit(
      f"[bold green]Analysis completed successfully![/bold green]\n\n"
      f"Report saved to:\n[bold]{output}[/bold]",
      border_style="green",
    )
  )

  _print_summary(state)


def _print_step_history(state) -> None:
  table = Table(title="Workflow Steps")

  table.add_column("Step", style="cyan")
  table.add_column("Status", style="green")
  table.add_column("Message")
  table.add_column("Metadata")

  for step in state.step_history:
    table.add_row(
      step.step_name,
      str(step.status),
      step.message or "",
      str(step.metadata or {}),
    )

  console.print(table)


def _print_summary(state) -> None:
  table = Table(title="Analysis Summary")

  table.add_column("Item", style="cyan")
  table.add_column("Value", style="white")

  if state.document is not None:
    table.add_row("Paper ID", state.document.metadata.paper_id or "Unknown")
    table.add_row("Title", state.document.metadata.title or "Unknown")
    table.add_row("Pages", str(len(state.document.pages)))
    table.add_row("Chunks", str(len(state.document.chunks)))

  if state.analysis_plan is not None:
    table.add_row("Tasks", str(len(state.analysis_plan.tasks)))
    table.add_row("Focus Questions", str(len(state.analysis_plan.focus_questions)))

  if state.evidence_bundle is not None:
    table.add_row("Evidence Items", str(len(state.evidence_bundle.items)))

  if state.final_report is not None:
    table.add_row("Report Sections", str(len(state.final_report.sections)))

  console.print(table)


if __name__ == "__main__":
    app()