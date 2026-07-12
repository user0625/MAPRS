import json
import time
from types import SimpleNamespace

from backend.api.ask_store import AskStore
from backend.api.task_store import DatabaseTaskStore
from backend.ask_paper import fallback_query, rewrite_question, sanitize_citations
from backend.ask_retrieval import (
    AskPaperRetrievalService,
    RetrievalCache,
    get_retrieval_service,
    terms,
)
from backend.core.config import AppSettings
from backend.llm.client import LLMResponse
from backend.tools.embedder import BaseEmbedder


def settings(**kwargs):
    return AppSettings(_env_file=None, project_root=".", **kwargs)


def write_state(path, chunks):
    path.write_text(json.dumps({"document": {"chunks": chunks}}), encoding="utf-8")


class SemanticEmbedder(BaseEmbedder):
    model_name = "semantic-test"

    def embed_text(self, text):
        if "automobile" in text or "car" in text:
            return [1.0, 0.0]
        return [0.0, 1.0]


class BrokenEmbedder(BaseEmbedder):
    model_name = "broken"

    def embed_text(self, text):
        raise RuntimeError("secret upstream details")


class RewriteClient:
    def __init__(self, content=None, error=None):
        self.content, self.error, self.calls = content, error, []

    def generate(self, messages, temperature=0.2, max_tokens=None):
        self.calls.append((messages, max_tokens))
        if self.error:
            raise self.error
        return LLMResponse(content=self.content, model="test", provider="test")


def test_terms_support_english_and_chinese_bigrams():
    assert terms("Neural Networks 神经网络") == ["neural", "networks", "神", "经", "网", "络", "神经", "经网", "网络"]


def test_bm25_section_filter_and_no_answer(tmp_path):
    state = tmp_path / "state.json"
    write_state(state, [
        {"chunk_id": "methods", "section": "Methods", "text": "gradient optimization training"},
        {"chunk_id": "results", "section": "Results", "text": "gradient optimization improved accuracy"},
    ])
    service = AskPaperRetrievalService(settings())
    result = service.retrieve("task", str(state), "gradient optimization", "Methods")
    assert [chunk["chunk_id"] for _, chunk in result.hits] == ["methods"]
    assert service.retrieve("task", str(state), "unfindable phrase", "Methods").hits == []
    assert result.diagnostics.vector_enabled is False


def test_vector_candidates_and_rrf_add_semantic_synonym(tmp_path):
    state = tmp_path / "state.json"
    write_state(state, [
        {"chunk_id": "semantic", "section": "A", "text": "automobile safety"},
        {"chunk_id": "lexical", "section": "A", "text": "car unrelated"},
    ])
    service = AskPaperRetrievalService(
        settings(embedding_provider="openai_compatible", embedding_model="semantic-test", embedding_api_key="x"),
        embedder=SemanticEmbedder(),
    )
    result = service.retrieve("task", str(state), "car safety")
    assert result.diagnostics.vector_enabled
    assert result.diagnostics.vector_candidates == 2
    assert {chunk["chunk_id"] for _, chunk in result.hits} == {"semantic", "lexical"}
    assert result.hits[0][0] > 0


def test_embedding_failure_degrades_to_bm25_without_sensitive_error(tmp_path):
    state = tmp_path / "state.json"
    write_state(state, [{"chunk_id": "c1", "text": "robust retrieval"}])
    service = AskPaperRetrievalService(
        settings(embedding_provider="openai_compatible", embedding_api_key="x"), embedder=BrokenEmbedder()
    )
    result = service.retrieve("task", str(state), "retrieval")
    assert result.hits
    assert result.diagnostics.degraded_reason == "embedding_unavailable:RuntimeError"
    assert "secret" not in result.diagnostics.degraded_reason


def test_cache_key_version_provider_section_and_lru_capacity(tmp_path):
    cache = RetrievalCache(maxsize=2)
    service = AskPaperRetrievalService(settings(), cache=cache)
    state = tmp_path / "state.json"
    write_state(state, [{"chunk_id": "c1", "section": "A", "text": "alpha"}])
    service.retrieve("one", str(state), "alpha", "A")
    service.retrieve("one", str(state), "alpha", None)
    assert len(cache.data) == 2
    time.sleep(0.001)
    write_state(state, [{"chunk_id": "c2", "section": "A", "text": "alpha beta"}])
    service.retrieve("one", str(state), "beta", "A")
    assert len(cache.data) == 2
    assert any(key[1] == state.stat().st_mtime_ns for key in cache.data)


def test_default_retrieval_service_reuses_worker_cache():
    configured = settings()
    assert get_retrieval_service(configured) is get_retrieval_service(configured)


def test_rewrite_uses_six_messages_and_falls_back_deterministically():
    recent = [SimpleNamespace(role="user" if i % 2 == 0 else "assistant", content=f"m{i}") for i in range(8)]
    client = RewriteClient("What method does the paper use?")
    rewritten, reason = rewrite_question(client, recent, "How does it work?", 160)
    assert rewritten == "What method does the paper use?"
    assert reason is None
    assert client.calls[0][1] == 160
    assert "m1" not in client.calls[0][0][1].content
    assert "m2" in client.calls[0][0][1].content

    broken = RewriteClient(error=TimeoutError())
    rewritten, reason = rewrite_question(broken, recent, "How does it work?", 160)
    assert rewritten == fallback_query(recent, "How does it work?") == "m6 How does it work?"
    assert reason == "rewrite_unavailable:TimeoutError"


def test_citation_whitelist_removes_cross_message_ids():
    allowed = {"msg_current:E1"}
    answer, citations = sanitize_citations(
        "Supported [msg_current:E1], forged [msg_other:E9] and unknown [msg_current:E7].", allowed
    )
    assert "msg_current:E1" in answer
    assert "msg_other:E9" not in answer
    assert "msg_current:E7" not in answer
    assert citations == ["msg_current:E1"]


def test_evidence_snapshot_is_separate_from_actual_citations(tmp_path):
    tasks = DatabaseTaskStore(f"sqlite:///{tmp_path / 'ask.db'}")
    tasks.create_tables()
    tasks.create_task("task", str(tmp_path / "paper.pdf"))
    ask = AskStore(tasks)
    conversation = ask.create_conversation("task")
    _, assistant = ask.create_exchange(conversation.id, "question", None, "en")
    snapshot = [
        {"evidence_id": f"{assistant.id}:E1", "task_id": "task", "chunk_id": "c1", "text": "one"},
        {"evidence_id": f"{assistant.id}:E2", "task_id": "task", "chunk_id": "c2", "text": "two"},
    ]
    ask.finish(assistant.id, "answer", snapshot, [snapshot[0]["evidence_id"], "msg_foreign:E1"])

    stored = ask.get_message(assistant.id)
    assert stored.citation_ids == [snapshot[0]["evidence_id"]]
    assert ask.evidence("task", snapshot[0]["evidence_id"]) is not None
    assert ask.evidence("task", snapshot[1]["evidence_id"]) is not None
