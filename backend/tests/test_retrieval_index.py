import json

import numpy as np
import pytest

from backend.api.task_store import DatabaseTaskStore
from backend.ask_retrieval import AskPaperRetrievalService
from backend.core.config import AppSettings
from backend.retrieval_index import INDEX_SCHEMA, index_path, save_vectors
from backend.tools.embedder import BaseEmbedder


class CountingEmbedder(BaseEmbedder):
    model_name = "fixture-vector"

    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def embed_text(self, text):
        self.calls += 1
        if self.fail:
            raise RuntimeError("must not call upstream")
        return [1.0, 0.0] if "alpha" in text else [0.0, 1.0]


def configured(tmp_path, **updates):
    values = dict(
        _env_file=None,
        project_root=tmp_path,
        embedding_provider="openai_compatible",
        embedding_model="fixture-vector",
        embedding_api_key="fixture",
        ask_index_dir="indexes",
    )
    values.update(updates)
    return AppSettings(**values)


def state_file(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"document": {"chunks": [
        {"chunk_id": "a", "text": "alpha private paper text", "section": "A", "page_start": 1, "page_end": 1},
        {"chunk_id": "b", "text": "beta private paper text", "section": "B", "page_start": 2, "page_end": 2},
    ]}}), encoding="utf-8")
    return path


def test_persistent_index_survives_restart_and_contains_no_body(tmp_path):
    settings = configured(tmp_path)
    state = state_file(tmp_path)
    first = CountingEmbedder()
    service = AskPaperRetrievalService(settings, embedder=first)
    cold = service.retrieve("task", str(state), "alpha", "A")
    assert cold.diagnostics.index_build_ms > 0
    assert cold.diagnostics.index_load_ms == 0
    assert cold.diagnostics.index_memory_cache_hit is False
    memory = service.retrieve("task", str(state), "alpha", "A")
    assert memory.diagnostics.index_build_ms == 0
    assert memory.diagnostics.index_load_ms == 0
    assert memory.diagnostics.index_memory_cache_hit is True
    persisted = index_path(settings, "task")
    assert persisted.is_file()
    payload = persisted.read_text(encoding="utf-8")
    assert "private paper text" not in payload
    assert json.loads(payload)["schema_version"] == INDEX_SCHEMA

    second = CountingEmbedder(fail=True)
    result = AskPaperRetrievalService(settings, embedder=second).retrieve(
        "task", str(state), "alpha", "A"
    )
    assert result.diagnostics.index_cache_hit is True
    assert result.diagnostics.index_build_ms == 0
    assert result.diagnostics.index_load_ms > 0
    assert result.diagnostics.index_memory_cache_hit is False
    assert second.calls == 1  # query only; document vectors came from disk


def test_corrupt_state_and_model_changes_invalidate_index(tmp_path):
    settings = configured(tmp_path)
    state = state_file(tmp_path)
    AskPaperRetrievalService(settings, embedder=CountingEmbedder()).retrieve(
        "task", str(state), "alpha"
    )
    index_path(settings, "task").write_text("{broken", encoding="utf-8")
    rebuilt = CountingEmbedder()
    result = AskPaperRetrievalService(settings, embedder=rebuilt).retrieve(
        "task", str(state), "alpha"
    )
    assert result.diagnostics.index_cache_hit is False
    assert rebuilt.calls >= 3

    changed = configured(tmp_path, embedding_model="changed-model")
    changed_embedder = CountingEmbedder()
    changed_embedder.model_name = "changed-model"
    AskPaperRetrievalService(changed, embedder=changed_embedder).retrieve(
        "task", str(state), "alpha"
    )
    assert changed_embedder.calls >= 3

    data = json.loads(state.read_text(encoding="utf-8"))
    data["metadata"] = {"changed": True}
    state.write_text(json.dumps(data), encoding="utf-8")
    changed_state_embedder = CountingEmbedder()
    changed_state_embedder.model_name = "changed-model"
    AskPaperRetrievalService(changed, embedder=changed_state_embedder).retrieve(
        "task", str(state), "alpha"
    )
    assert changed_state_embedder.calls >= 3


def test_atomic_write_preserves_existing_index_on_replace_failure(tmp_path, monkeypatch):
    settings = configured(tmp_path)
    vectors = np.asarray([[1.0, 0.0]], dtype=np.float32)
    kwargs = dict(
        state_sha256="a" * 64,
        chunk_sha256="b" * 64,
        chunk_ids=["c1"],
        vectors=vectors,
    )
    save_vectors(settings, "task", **kwargs)
    before = index_path(settings, "task").read_bytes()
    monkeypatch.setattr("backend.retrieval_index.os.replace", lambda *_: (_ for _ in ()).throw(OSError("stop")))
    with pytest.raises(OSError):
        save_vectors(settings, "task", **kwargs)
    assert index_path(settings, "task").read_bytes() == before
    assert not list(index_path(settings, "task").parent.glob(".*.tmp"))


def test_task_delete_and_retention_remove_index(tmp_path, monkeypatch):
    settings = configured(tmp_path)
    monkeypatch.setattr("backend.core.config.get_settings", lambda: settings)
    path = index_path(settings, "task")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    store = DatabaseTaskStore(f"sqlite:///{tmp_path / 'tasks.db'}")
    store.create_tables()
    store.create_task("task", str(tmp_path / "task_input.pdf"))
    store.mark_failed("task", "failed")
    assert store.soft_delete("task") is True
    assert not path.exists()
