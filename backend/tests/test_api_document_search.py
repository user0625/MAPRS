import json

import anyio
import httpx2
import pytest

from backend.api.main import create_app
from backend.api.task_store import DatabaseTaskStore
from backend.core.config import AppSettings


class ASGIClient:
    def __init__(self, app):
        self.app = app

    def request(self, method, url, **kwargs):
        async def send():
            transport = httpx2.ASGITransport(app=self.app)
            async with httpx2.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                return await client.request(method, url, **kwargs)
        return anyio.run(send)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)


@pytest.fixture()
def search_api(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'search.sqlite3'}"
    settings = AppSettings(
        _env_file=None,
        project_root=tmp_path,
        database_url=database_url,
        embedding_provider="mock",
    )
    store = DatabaseTaskStore(database_url)
    store.create_tables()
    monkeypatch.setattr("backend.api.main.get_settings", lambda: settings)
    monkeypatch.setattr("backend.api.routes.search.get_settings", lambda: settings)
    monkeypatch.setattr("backend.api.task_store.task_store", store)
    monkeypatch.setattr("backend.api.routes.tasks.task_store", store)

    def completed(task_id="done", *, state_payload=None, pages=3):
        path = tmp_path / f"{task_id}.json"
        payload = state_payload or {"document": {
            "sections": [{"name": "Methods"}, {"name": "Results"}],
            "pages": [{} for _ in range(pages)],
            "chunks": [
                {"chunk_id": "m1", "text": "alpha target method", "section": "Methods", "page_start": 1, "page_end": 1},
                {"chunk_id": "m2", "text": "target context", "section": "Methods", "page_start": 2, "page_end": 2},
                {"chunk_id": "r1", "text": "target result", "section": "Results", "page_start": 3, "page_end": 3},
            ],
        }}
        path.write_text(json.dumps(payload), encoding="utf-8")
        report = tmp_path / f"{task_id}.md"
        report.write_text("# report", encoding="utf-8")
        store.create_task(task_id, str(tmp_path / f"{task_id}.pdf"))
        store.mark_completed(
            task_id, str(report), str(path), metadata={"num_pages": pages}
        )
        return path

    return ASGIClient(create_app()), store, completed, tmp_path


def test_search_status_state_scope_and_validation_errors(search_api):
    client, store, completed, tmp_path = search_api
    store.create_task("pending", str(tmp_path / "pending.pdf"))
    assert client.post("/api/tasks/missing/search", json={"query": "x"}).status_code == 404
    assert client.post("/api/tasks/pending/search", json={"query": "x"}).status_code == 409

    completed("done")
    assert client.post("/api/tasks/done/search", json={"query": "   "}).status_code == 422
    assert client.post("/api/tasks/done/search", json={"query": "x", "top_k": 21}).status_code == 422
    assert client.post("/api/tasks/done/search", json={"query": "x", "page_start": 1}).status_code == 422
    assert client.post("/api/tasks/done/search", json={"query": "x", "page_start": 3, "page_end": 2}).status_code == 422
    assert client.post("/api/tasks/done/search", json={"query": "x", "section": "Unknown"}).status_code == 422
    assert client.post("/api/tasks/done/search", json={"query": "x", "page_start": 1, "page_end": 4}).status_code == 422

    missing = completed("missing-state")
    missing.unlink()
    assert client.post("/api/tasks/missing-state/search", json={"query": "x"}).status_code == 404
    broken = completed("broken-state")
    broken.write_text("{private broken", encoding="utf-8")
    response = client.post("/api/tasks/broken-state/search", json={"query": "x"})
    assert response.status_code == 500
    assert "private broken" not in response.text


def test_bm25_search_response_context_empty_and_openapi_schema(search_api):
    client, _, completed, _ = search_api
    completed("done")
    response = client.post("/api/tasks/done/search", json={
        "query": "  target  ", "mode": "bm25", "section": "Methods",
        "page_start": 1, "page_end": 2, "top_k": 2,
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["query"] == "target"
    assert payload["mode_used"] == "bm25"
    assert payload["diagnostics"] == {
        **payload["diagnostics"],
        "actual_mode": "bm25",
        "index_source": "unavailable",
        "fallback_reason": None,
    }
    assert len(payload["hits"]) == 2
    assert payload["hits"][0]["context"]
    assert "vectors" not in response.text and "api_key" not in response.text

    empty = client.post("/api/tasks/done/search", json={"query": "not-present"})
    assert empty.status_code == 200 and empty.json()["hits"] == []
    operation = client.get("/openapi.json").json()["paths"]["/api/tasks/{task_id}/search"]["post"]
    assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("DocumentSearchRequest")
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("DocumentSearchResponse")
