import json

import anyio
import httpx2
import pytest
from sqlmodel import Session, func, select

from backend.api.ask_store import (
    AskStore,
    MessageEvidence,
    MessageStreamEvent,
    PaperConversation,
    PaperMessage,
)
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

    def completed(task_id: str, sections=("Methods",), page_count: int = 0):
        state = tmp_path / f"{task_id}.json"
        state.write_text(json.dumps({
            "document": {
                "sections": [{"name": name} for name in sections],
                "pages": [{} for _ in range(page_count)],
            },
            "evidence_bundle": {"items": []},
        }), encoding="utf-8")
        report = tmp_path / f"{task_id}.md"
        report.write_text("# report", encoding="utf-8")
        store.create_task(task_id, str(tmp_path / f"{task_id}.pdf"))
        store.mark_completed(
            task_id,
            str(report),
            str(state),
            paper_id=task_id,
            metadata={"num_pages": page_count},
        )
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


def test_page_range_validation_persistence_and_retry_scope(ask_api):
    client, _, ask, queued, completed = ask_api
    completed("task-a", page_count=12)
    cid = client.post("/api/tasks/task-a/conversations", json={}).json()["id"]

    endpoint = f"/api/conversations/{cid}/messages"
    assert client.post(endpoint, json={"content": "x", "page_start": 2}).status_code == 422
    assert client.post(endpoint, json={
        "content": "x", "page_start": 5, "page_end": 4,
    }).status_code == 422
    assert client.post(endpoint, json={
        "content": "x", "page_start": 1, "page_end": 13,
    }).status_code == 422

    accepted = client.post(endpoint, json={
        "content": "Scoped question", "section": "Methods",
        "page_start": 2, "page_end": 4, "language": "en",
    })
    assert accepted.status_code == 202
    detail = client.get(f"/api/conversations/{cid}").json()
    assert [(item["page_start"], item["page_end"]) for item in detail["messages"]] == [
        (2, 4), (2, 4),
    ]

    answer_id = accepted.json()["assistant_message_id"]
    ask.mark_canceled(answer_id)
    retried = client.post(f"/api/conversations/{cid}/messages/{answer_id}/retry")
    assert retried.status_code == 202
    retry = ask.get_message(retried.json()["assistant_message_id"])
    assert retry and (retry.page_start, retry.page_end) == (2, 4)
    assert queued == [answer_id, retry.id]


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


def test_conversation_search_is_literal_case_insensitive_and_task_scoped(ask_api):
    client, _, ask, _, completed = ask_api
    completed("task-a")
    completed("task-b")
    title_match = ask.create_conversation("task-a", "Transformer NOTES")
    content_match = ask.create_conversation("task-a", "Other")
    percent_match = ask.create_conversation("task-a", "Accuracy 100%_literal")
    assistant_match = ask.create_conversation("task-a", "Answer match")
    isolated = ask.create_conversation("task-b", "Transformer notes")
    ask.create_exchange(title_match.id, "also transformer", None, "auto")
    ask.create_exchange(content_match.id, "Discuss TRANSFORMER scaling", None, "auto")
    _, assistant = ask.create_exchange(assistant_match.id, "What is novel?", None, "auto")
    ask.finish(assistant.id, "A Transformer-specific answer", [], [])

    found = client.get("/api/tasks/task-a/conversations?search=transformer").json()["items"]
    assert {item["id"] for item in found} == {
        title_match.id,
        content_match.id,
        assistant_match.id,
    }
    assert len(found) == 3  # A title and message match still returns one row.
    assert isolated.id not in {item["id"] for item in found}
    assert [item["id"] for item in client.get(
        "/api/tasks/task-a/conversations?search=%25"
    ).json()["items"]] == [percent_match.id]
    assert [item["id"] for item in client.get(
        "/api/tasks/task-a/conversations?search=_literal"
    ).json()["items"]] == [percent_match.id]
    assert len(client.get("/api/tasks/task-a/conversations?search=%20%20").json()["items"]) == 4


def test_delete_conversation_cleans_graph_and_preserves_neighbors(ask_api):
    client, store, ask, _, completed = ask_api
    completed("task-a")
    doomed = ask.create_conversation("task-a", "Delete me")
    kept = ask.create_conversation("task-a", "Keep me")
    _, answer = ask.create_exchange(doomed.id, "question", None, "en")
    ask.append_event(answer.id, "token", {"token": "answer"})

    conflict = client.request("DELETE", f"/api/conversations/{doomed.id}")
    assert conflict.status_code == 409
    ask.finish(answer.id, "answer", [{
        "evidence_id": "ev-delete", "task_id": "task-a", "text": "source",
        "chunk_id": "chunk-delete", "page_start": 4, "page_end": 5,
        "section": "Results", "score": 0.9,
    }], ["ev-delete"])

    assert client.request("DELETE", f"/api/conversations/{doomed.id}").status_code == 204
    assert client.get(f"/api/conversations/{doomed.id}").status_code == 404
    assert client.get(f"/api/conversations/{kept.id}").status_code == 200
    assert store.get_task("task-a") is not None
    with Session(store.engine) as session:
        assert session.exec(select(PaperMessage).where(PaperMessage.conversation_id == doomed.id)).first() is None
        assert session.exec(select(MessageEvidence).where(MessageEvidence.message_id == answer.id)).first() is None
        assert session.exec(select(MessageStreamEvent).where(MessageStreamEvent.message_id == answer.id)).first() is None
    assert client.request("DELETE", "/api/conversations/missing").status_code == 404


def test_task_delete_reuses_complete_conversation_cleanup(ask_api):
    client, store, ask, _, completed = ask_api
    completed("task-a")
    conversation = ask.create_conversation("task-a")
    _, answer = ask.create_exchange(conversation.id, "question", None, "auto")
    ask.finish(answer.id, "answer", [{
        "evidence_id": "ev-task-delete", "task_id": "task-a", "text": "source",
    }], ["ev-task-delete"])

    assert client.request("DELETE", "/api/tasks/task-a").status_code == 204
    with Session(store.engine) as session:
        for model in (PaperConversation, PaperMessage, MessageEvidence, MessageStreamEvent):
            assert session.exec(select(func.count()).select_from(model)).one() == 0


def test_conversation_markdown_and_json_archives_only_cited_evidence(ask_api):
    client, _, ask, _, completed = ask_api
    completed("task-a")
    conversation = ask.create_conversation("task-a", "研究 notes", "zh")
    _, answer = ask.create_exchange(
        conversation.id, "What changed?", "Methods", "en", 2, 3
    )
    ask.finish(answer.id, "It improved.", [
        {
            "evidence_id": "ev-used", "task_id": "task-a", "text": "Cited passage",
            "chunk_id": "chunk-1", "page_start": 2, "page_end": 3,
            "section": "Methods", "score": 0.875,
        },
        {
            "evidence_id": "ev-unused", "task_id": "task-a", "text": "Candidate only",
            "chunk_id": "chunk-2", "page_start": 9, "page_end": 9,
            "section": "Appendix", "score": 0.1,
        },
    ], ["ev-used"])
    _, failed = ask.create_exchange(conversation.id, "And limits?", None, "auto")
    ask.fail(failed.id, "provider unavailable")

    json_response = client.get(f"/api/conversations/{conversation.id}/artifacts/json")
    assert json_response.status_code == 200
    assert json_response.headers["content-type"].startswith("application/json; charset=utf-8")
    assert json_response.headers["content-disposition"] == f'attachment; filename="ask-paper-{conversation.id}.json"'
    archive = json_response.json()
    assert archive["schema_version"] == "ask-paper-conversation-v1"
    assert archive["conversation"]["task_id"] == "task-a"
    assert [message["role"] for message in archive["messages"]] == [
        "user", "assistant", "user", "assistant"
    ]
    assert archive["messages"][-1]["status"] == "failed"
    assert archive["messages"][0]["page_start"] == 2
    assert archive["messages"][0]["page_end"] == 3
    assert [item["evidence_id"] for item in archive["evidence"]] == ["ev-used"]
    assert archive["evidence"][0]["message_id"] == answer.id
    assert archive["evidence"][0]["score"] == 0.875

    markdown = client.get(f"/api/conversations/{conversation.id}/artifacts/markdown")
    assert markdown.status_code == 200
    assert markdown.headers["content-type"].startswith("text/markdown; charset=utf-8")
    assert "# 研究 notes" in markdown.text
    assert "**Task ID:** `task-a`" in markdown.text
    assert "**Status:** `failed`" in markdown.text
    assert "**Citation IDs:** `ev-used`" in markdown.text
    assert "**Pages:** `2–3`" in markdown.text
    assert "### ev-used" in markdown.text and "Cited passage" in markdown.text
    assert "ev-unused" not in markdown.text and "Candidate only" not in markdown.text

    ask.create_exchange(conversation.id, "still working", None, "auto")
    assert client.get(f"/api/conversations/{conversation.id}/artifacts/json").status_code == 409
