import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.main import create_app


def _completed_task(store, tmp_path: Path, task_id: str):
    state = tmp_path / f"{task_id}.json"
    state.write_text(json.dumps({"document":{"metadata":{"paper_id":f"paper-{task_id}","title":f"Paper {task_id}","authors":["Author"],"year":2025},"chunks":[{"chunk_id":f"{task_id}-c1","text":"Method dataset result limitation baseline metric.","page_start":1,"page_end":1,"section":"Method"}]}}),encoding="utf-8")
    report = tmp_path / f"{task_id}.md"
    report.write_text("report",encoding="utf-8")
    pdf = tmp_path / f"{task_id}.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    store.create_task(task_id,str(pdf))
    store.mark_completed(task_id,str(report),str(state),f"Paper {task_id}",f"paper-{task_id}")


def test_comparison_api_validation_creation_recovery_and_source_delete_guard(tmp_path,monkeypatch):
    monkeypatch.setattr("backend.worker.tasks.enqueue_comparison",lambda comparison_id:"celery-test")
    app=create_app()
    from backend.api import task_store as module
    module.task_store.create_tables()
    client=TestClient(app)
    prefix=tmp_path.name
    task_ids=[f"{prefix}-a",f"{prefix}-b",f"{prefix}-c"]
    for task_id in task_ids:
        _completed_task(module.task_store,tmp_path,task_id)

    duplicate=client.post("/api/comparisons",json={"task_ids":[task_ids[0],task_ids[0]]})
    assert duplicate.status_code==422
    too_few=client.post("/api/comparisons",json={"task_ids":[task_ids[0]]})
    assert too_few.status_code==422
    created=client.post("/api/comparisons",json={"task_ids":[task_ids[1],task_ids[0]],"title":"Ordered comparison","focus":"methods","language":"en"})
    assert created.status_code==202
    body=created.json()
    assert body["status"]=="pending"
    assert [paper["source_task_id"] for paper in body["papers"]]==[task_ids[1],task_ids[0]]

    detail=client.get(f"/api/comparisons/{body['id']}")
    assert detail.status_code==200
    assert detail.json()["last_event_id"]==1

    blocked=client.delete(f"/api/tasks/{task_ids[0]}")
    assert blocked.status_code==409
    assert "active comparisons" in blocked.json()["detail"]
    assert client.post(f"/api/comparisons/{body['id']}/cancel").json()["status"]=="canceled"
    assert client.delete(f"/api/comparisons/{body['id']}").status_code==204


def test_comparison_api_rejects_unfinished_or_missing_artifacts(tmp_path,monkeypatch):
    app=create_app()
    from backend.api import task_store as module
    module.task_store.create_tables()
    client=TestClient(app)
    pending=f"{tmp_path.name}-pending"
    module.task_store.create_task(pending,str(tmp_path/"pending.pdf"))
    response=client.post("/api/comparisons",json={"task_ids":[pending,f"{tmp_path.name}-missing"]})
    assert response.status_code==409
    assert "not completed" in response.json()["detail"]
