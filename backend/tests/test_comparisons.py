import json
from pathlib import Path

from backend.api.comparison_store import ComparisonStatus, ComparisonStore
from backend.api.task_store import DatabaseTaskStore
from backend.comparisons.exporter import ComparisonExporter
from backend.comparisons.service import build_comparison
from backend.core.config import AppSettings


def _state(path: Path, task_id: str, title: str, year: int) -> Path:
    data = {
        "document": {
            "metadata": {"paper_id": f"paper-{task_id}", "title": title, "authors": [f"Author {task_id}"], "year": year},
            "chunks": [
                {"chunk_id": f"{task_id}-method", "text": f"Our method model architecture uses dataset {task_id} and baseline comparison.", "page_start": 2, "page_end": 2, "section": "Method"},
                {"chunk_id": f"{task_id}-result", "text": "Results improve accuracy and AUC performance. A limitation remains future work.", "page_start": 5, "page_end": 5, "section": "Results"},
            ],
        }
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _setup(tmp_path):
    tasks = DatabaseTaskStore(f"sqlite:///{tmp_path / 'tasks.db'}")
    tasks.create_tables()
    papers = []
    for index in range(2):
        task_id = f"task-{index}"
        state = _state(tmp_path / f"{task_id}.json", task_id, f"Paper {index}", 2020 + index)
        report = tmp_path / f"{task_id}.md"
        report.write_text("report", encoding="utf-8")
        tasks.create_task(task_id, str(tmp_path / f"{task_id}.pdf"))
        tasks.mark_completed(task_id, str(report), str(state), f"Paper {index}", f"paper-{task_id}")
        papers.append({"source_task_id": task_id, "paper_id": f"paper-{task_id}", "title": f"Paper {index}", "authors": [f"Author {index}"], "year": 2020 + index, "state_json_path": str(state)})
    return tasks, papers


def test_comparison_store_snapshots_events_and_active_source_guard(tmp_path):
    tasks, papers = _setup(tmp_path)
    store = ComparisonStore(tasks)
    store.create("cmp-one", "Two papers", "methods and results", "en", papers)
    assert [paper.source_task_id for paper in store.papers("cmp-one")] == ["task-0", "task-1"]
    assert store.active_for_task("task-0") == ["cmp-one"]
    assert store.claim("cmp-one") is True
    store.progress("cmp-one", "retrieve_evidence", 35, "Retrieving")
    assert [event.sequence for event in store.events("cmp-one")] == [1, 2, 3]
    store.cancel("cmp-one")
    assert store.get("cmp-one").status == ComparisonStatus.CANCELED
    assert store.active_for_task("task-0") == []


def test_comparison_report_schema_namespace_budget_and_five_exports(tmp_path):
    tasks, papers = _setup(tmp_path)
    store = ComparisonStore(tasks)
    store.create("cmp-report", "Comparison", "method dataset results", "en", papers)
    report, evidence = build_comparison(
        "cmp-report",
        store,
        AppSettings(_env_file=None, comparison_evidence_per_paper=2, comparison_paper_max_tokens=256),
    )
    assert report["schema_version"] == "paper-comparison-v1"
    assert [paper["source_task_id"] for paper in report["source_papers"]] == ["task-0", "task-1"]
    assert len(report["matrix"]) == 7
    assert all(len(row["cells"]) == 2 for row in report["matrix"])
    assert len(evidence) <= 4
    assert all(item.evidence_id.startswith("cmp-report:ev:") for item in evidence)
    assert all(item.source_task_id in {"task-0", "task-1"} for item in evidence)
    whitelist = {item.evidence_id for item in evidence}
    assert set(report["evidence_ids"]) == whitelist
    paths = ComparisonExporter().save_all(report, tmp_path / "exports", "cmp-report")
    assert set(paths) == {"markdown", "json", "html", "pdf", "docx"}
    assert all(Path(path).is_file() and Path(path).stat().st_size > 0 for path in paths.values())


def test_terminal_comparison_keeps_snapshots_after_source_files_are_removed(tmp_path):
    tasks, papers = _setup(tmp_path)
    store = ComparisonStore(tasks)
    store.create("cmp-snapshot", "Comparison", "method", "en", papers)
    report, evidence = build_comparison("cmp-snapshot", store, AppSettings(_env_file=None))
    store.save_evidence(evidence)
    artifacts = ComparisonExporter().save_all(report, tmp_path / "exports", "cmp-snapshot")
    store.complete("cmp-snapshot", artifacts["markdown"], artifacts["json"], artifacts)
    for paper in papers:
        Path(paper["state_json_path"]).unlink()
    assert store.evidence("cmp-snapshot", evidence[0].evidence_id).text
    assert json.loads(Path(store.get("cmp-snapshot").structured_path).read_text())["schema_version"] == "paper-comparison-v1"
    assert store.delete("cmp-snapshot") is True
    assert store.get("cmp-snapshot") is None
