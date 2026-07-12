import json

import anyio
import httpx2
import pytest

from backend.api.ask_store import AskStore
from backend.api.main import create_app
from backend.api.task_store import DatabaseTaskStore
from backend.core.config import AppSettings


class ASGIClient:
    """Synchronous facade over ASGITransport, avoiding TestClient portal hangs."""

    def __init__(self, app):
        self.app = app

    def request(self, method: str, url: str, **kwargs):
        async def send():
            transport = httpx2.ASGITransport(app=self.app)
            async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.request(method, url, **kwargs)
        return anyio.run(send)

    def get(self, url: str, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs):
        return self.request("POST", url, **kwargs)


@pytest.fixture()
def ask_api(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'ask.sqlite3'}"
    settings = AppSettings(
        _env_file=None,
        project_root=tmp_path,
        database_url=database_url,
        sse_heartbeat_seconds=1,
    )
    store = DatabaseTaskStore(database_url)
    monkeypatch.setattr("backend.api.main.get_settings", lambda: settings)
    monkeypatch.setattr("backend.api.routes.conversations.get_settings", lambda: settings)
    monkeypatch.setattr("backend.api.task_store.task_store", store)
    monkeypatch.setattr("backend.api.routes.tasks.task_store", store)
    queued: list[str] = []
    monkeypatch.setattr("backend.worker.tasks.enqueue_answer", queued.append)

    def completed(task_id: str, sections=("Methods",)):
        state = tmp_path / f"{task_id}.json"
        state.write_text(json.dumps({
            "document": {"sections": [{"name": name} for name in sections]},
            "evidence_bundle": {"items": []},
        }), encoding="utf-8")
        report = tmp_path / f"{task_id}.md"
        report.write_text("# report", encoding="utf-8")
        store.create_task(task_id, str(tmp_path / f"{task_id}.pdf"))
        store.mark_completed(task_id, str(report), str(state), paper_id=task_id)
        return task_id

    # Run the application's lifespan work explicitly. Starlette's context-managed
    # TestClient deadlocks with the dependency versions used by this repository.
    store.create_tables()
    store.recover_interrupted_tasks(settings.task_stale_after_seconds)
    store.cleanup_expired_files(settings.file_retention_days)
    client = ASGIClient(create_app())
    yield client, store, AskStore(store), queued, completed


def test_tasks_conversations_and_messages_are_isolated(ask_api):
    client, _, _, queued, completed = ask_api
    completed("task-a")
    completed("task-b")
    conv_a = client.post("/api/tasks/task-a/conversations", json={"language": "en"}).json()
    conv_b = client.post("/api/tasks/task-b/conversations", json={}).json()

    assert [x["id"] for x in client.get("/api/tasks/task-a/conversations").json()["items"]] == [conv_a["id"]]
    assert conv_b["id"] not in {x["id"] for x in client.get("/api/tasks/task-a/conversations").json()["items"]}
    accepted = client.post(f"/api/conversations/{conv_a['id']}/messages", json={
        "content": "How?", "section": "Methods", "language": "en",
    })
    assert accepted.status_code == 202
    assert queued == [accepted.json()["assistant_message_id"]]
    assert client.post(f"/api/conversations/{conv_b['id']}/messages/{accepted.json()['assistant_message_id']}/cancel").status_code == 404


def test_validation_pagination_and_status_errors(ask_api):
    client, store, ask, _, completed = ask_api
    store.create_task("pending", "/tmp/pending.pdf")
    assert client.post("/api/tasks/missing/conversations", json={}).status_code == 404
    assert client.post("/api/tasks/pending/conversations", json={}).status_code == 409
    completed("task-a")
    cid = client.post("/api/tasks/task-a/conversations", json={}).json()["id"]
    assert client.post(f"/api/conversations/{cid}/messages", json={"content": "x", "section": "Results"}).status_code == 422
    assert client.post(f"/api/conversations/{cid}/messages", json={"content": ""}).status_code == 422
    for question in ("one", "two"):
        ask.create_exchange(cid, question, None, "auto")
    page = client.get(f"/api/conversations/{cid}?limit=2&offset=1")
    assert page.status_code == 200
    assert page.json()["total"] == 4
    assert len(page.json()["messages"]) == 2
    assert client.get(f"/api/conversations/{cid}?limit=0").status_code == 422


def test_cancel_failed_retry_and_conflicts(ask_api):
    client, _, ask, queued, completed = ask_api
    completed("task-a")
    cid = client.post("/api/tasks/task-a/conversations", json={}).json()["id"]
    _, generating = ask.create_exchange(cid, "question", None, "auto")
    canceled = client.post(f"/api/conversations/{cid}/messages/{generating.id}/cancel")
    assert canceled.status_code == 200
    assert ask.is_canceled(generating.id)
    assert client.post(f"/api/conversations/{cid}/messages/{generating.id}/cancel").status_code == 200
    ask.mark_canceled(generating.id)
    retry = client.post(f"/api/conversations/{cid}/messages/{generating.id}/retry")
    assert retry.status_code == 202
    assert queued[-1] == retry.json()["assistant_message_id"]
    assert client.post(f"/api/conversations/{cid}/messages/{retry.json()['assistant_message_id']}/retry").status_code == 409

    _, failed = ask.create_exchange(cid, "failure", "Methods", "zh")
    ask.fail(failed.id, "provider unavailable")
    retried = client.post(f"/api/conversations/{cid}/messages/{failed.id}/retry")
    assert retried.status_code == 202
    row = ask.get_message(retried.json()["assistant_message_id"])
    assert row and row.retry_of == failed.id and row.section == "Methods" and row.language == "zh"


def test_evidence_cannot_cross_task_boundary(ask_api):
    client, _, ask, _, completed = ask_api
    completed("task-a")
    completed("task-b")
    cid = client.post("/api/tasks/task-a/conversations", json={}).json()["id"]
    _, message = ask.create_exchange(cid, "question", None, "auto")
    ask.finish(message.id, "answer", [{
        "evidence_id": "ev-a", "task_id": "task-a", "text": "source",
        "chunk_id": "chunk-a", "page_start": 1, "page_end": 1, "section": "Methods",
    }], ["ev-a"])
    assert client.get("/api/tasks/task-a/evidence/ev-a").status_code == 200
    assert client.get("/api/tasks/task-b/evidence/ev-a").status_code == 404


def test_sse_after_header_order_and_terminal_close(ask_api):
    client, _, ask, _, completed = ask_api
    completed("task-a")
    cid = client.post("/api/tasks/task-a/conversations", json={}).json()["id"]
    _, message = ask.create_exchange(cid, "question", None, "auto")
    ask.append_event(message.id, "token", {"token": "A"})
    ask.append_event(message.id, "token", {"token": "B"})
    ask.finish(message.id, "AB", [], [])

    response = client.get(
        f"/api/conversations/{cid}/messages/{message.id}/events?after=1",
        headers={"Last-Event-ID": "2"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "id: 1" not in response.text and "id: 2" not in response.text
    assert "id: 3" in response.text and "event: completed" in response.text
    assert response.text.count("event: completed") == 1

    all_events = client.get(f"/api/conversations/{cid}/messages/{message.id}/events").text
    assert all_events.index('"token": "A"') < all_events.index('"token": "B"') < all_events.index("event: completed")
    assert ask.get_message(message.id).content == "AB"
