from pathlib import Path

from fastapi import BackgroundTasks
from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.api.task_store import APITaskStatus, task_store
from backend.core.config import AppSettings

client = TestClient(create_app())


def test_health_check():
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["service"] == "multi-agent-paper-reader"


def test_api_documentation_routes():
    assert client.get("/docs").status_code == 200
    openapi_response = client.get("/openapi.json")
    assert openapi_response.status_code == 200
    assert "/api/health" in openapi_response.json()["paths"]
    paths = openapi_response.json()["paths"]
    structured = paths["/api/tasks/{task_id}/report/structured"]["get"]
    evidence = paths["/api/tasks/{task_id}/evidence/{evidence_id}"]["get"]
    assert structured["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("StructuredReportResponse")
    assert evidence["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("EvidenceResponse")


def test_synchronous_upload_rejects_non_pdf():
    response = client.post(
        "/api/analyze/upload",
        files={"file": ("notes.txt", b"not a pdf", "text/plain")},
    )

    assert response.status_code == 400


def test_create_task_returns_pending_and_uses_configured_upload_dir(tmp_path, monkeypatch):
    settings = AppSettings(
        _env_file=None,
        project_root=tmp_path,
        output_dir=Path("runtime/output"),
        report_dir=Path("runtime/reports"),
        log_dir=Path("runtime/logs"),
    )
    monkeypatch.setattr("backend.api.routes.tasks.get_settings", lambda: settings)
    monkeypatch.setattr(BackgroundTasks, "add_task", lambda self, func, *args, **kwargs: None)

    response = client.post(
        "/api/tasks/analyze",
        files={"file": ("paper.pdf", b"%PDF-1.4 dummy", "application/pdf")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == APITaskStatus.PENDING
    record = task_store.get_task(payload["task_id"])
    assert record is not None
    assert Path(record.input_pdf_path).parent == tmp_path / "runtime/output/uploads"
    assert Path(record.input_pdf_path).exists()


def test_create_task_rejects_non_pdf(tmp_path):
    text_file = tmp_path / "test.txt"
    text_file.write_text("not a pdf", encoding="utf-8")

    with text_file.open("rb") as f:
        response = client.post(
            "/api/tasks/analyze",
            files={"file": ("test.txt", f, "text/plain")},
            data={
                "query": "Analyze this file.",
                "language": "zh",
            },
        )

    assert response.status_code == 400
    assert "Only PDF files are supported" in response.json()["detail"]


def test_get_missing_task_returns_404():
    response = client.get("/api/tasks/not_exists")

    assert response.status_code == 404
    assert "Task not found" in response.json()["detail"]


def test_get_report_before_completed_returns_409(tmp_path):
    task_id = "task_test_pending"
    pdf_path = tmp_path / "dummy.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 dummy")

    task_store.create_task(
        task_id=task_id,
        input_pdf_path=str(pdf_path),
    )

    response = client.get(f"/api/tasks/{task_id}/report")

    assert response.status_code == 409
    assert "Task is not completed" in response.json()["detail"]
