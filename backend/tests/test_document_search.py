import json

from backend.ask_retrieval import AskPaperRetrievalService, RetrievalCache
from backend.core.config import AppSettings
from backend.document_search import DocumentSearchService, bounded_snippet
from backend.tools.embedder import BaseEmbedder


class SearchEmbedder(BaseEmbedder):
    model_name = "search-test"

    def embed_text(self, text):
        if "semantic" in text or "meaning" in text:
            return [1.0, 0.0]
        return [0.0, 1.0]


class BrokenQueryEmbedder(SearchEmbedder):
    def embed_query(self, query):
        raise TimeoutError("private upstream response")


def configured(tmp_path, **updates):
    values = {
        "_env_file": None,
        "project_root": tmp_path,
        "embedding_provider": "openai_compatible",
        "embedding_model": "search-test",
        "embedding_api_key": "fixture",
        "ask_index_dir": "indexes",
    }
    values.update(updates)
    return AppSettings(**values)


def state_file(tmp_path, chunks):
    path = tmp_path / "state.json"
    state = {"document": {"chunks": chunks}}
    path.write_text(json.dumps(state), encoding="utf-8")
    return path, state


def test_bm25_ranking_top_k_stable_ties_and_scores(tmp_path):
    path, state = state_file(tmp_path, [
        {"chunk_id": "first", "text": "target alpha", "section": "A"},
        {"chunk_id": "second", "text": "target beta", "section": "A"},
        {"chunk_id": "none", "text": "unrelated", "section": "A"},
    ])
    result = DocumentSearchService(configured(tmp_path, embedding_provider="mock")).search(
        "task", str(path), state, "target", mode="bm25", top_k=1
    )
    assert result.mode_used == "bm25"
    assert [hit["chunk_id"] for hit in result.hits] == ["first"]
    assert result.hits[0]["sources"] == ["bm25"]
    assert result.hits[0]["bm25_score"] > 0
    assert result.hits[0]["vector_score"] is None
    assert result.hits[0]["hybrid_score"] is None


def test_hybrid_search_reuses_index_and_maps_sources(tmp_path):
    path, state = state_file(tmp_path, [
        {"chunk_id": "semantic", "text": "semantic passage", "section": "A"},
        {"chunk_id": "lexical", "text": "meaning appears here", "section": "A"},
    ])
    settings = configured(tmp_path)
    retrieval = AskPaperRetrievalService(
        settings, embedder=SearchEmbedder(), cache=RetrievalCache(2)
    )
    service = DocumentSearchService(settings, retrieval)
    first = service.search("task", str(path), state, "meaning", mode="auto")
    second = service.search("task", str(path), state, "meaning", mode="auto")
    assert first.mode_used == "hybrid"
    assert first.index_source == "cold_build"
    assert second.index_source == "memory_hit"
    assert first.hits[0]["hybrid_score"] is not None
    assert {source for hit in first.hits for source in hit["sources"]} == {
        "bm25", "vector"
    }


def test_auto_mock_is_bm25_but_query_failure_is_degraded(tmp_path):
    chunks = [{"chunk_id": "one", "text": "robust target", "section": "A"}]
    path, state = state_file(tmp_path, chunks)
    offline = DocumentSearchService(
        configured(tmp_path, embedding_provider="mock", embedding_api_key=None)
    ).search("task", str(path), state, "target")
    assert offline.mode_used == "bm25"
    assert offline.fallback_reason is None

    settings = configured(tmp_path)
    broken = DocumentSearchService(
        settings, AskPaperRetrievalService(settings, embedder=BrokenQueryEmbedder())
    ).search("task-2", str(path), state, "target")
    assert broken.mode_used == "degraded_to_bm25"
    assert broken.hits
    assert broken.fallback_reason == "query_embedding_unavailable"
    assert "private" not in broken.fallback_reason


def test_context_obeys_direct_adjacency_section_pages_and_deduplicates(tmp_path):
    path, state = state_file(tmp_path, [
        {"chunk_id": "before", "text": "before target", "section": "Methods", "page_start": 1, "page_end": 1},
        {"chunk_id": "hit", "text": "target " * 300, "section": "Methods", "page_start": 2, "page_end": 2},
        {"chunk_id": "other", "text": "after target", "section": "Results", "page_start": 2, "page_end": 2},
    ])
    result = DocumentSearchService(configured(tmp_path, embedding_provider="mock")).search(
        "task", str(path), state, "target", mode="bm25", section="Methods",
        page_start=2, page_end=2, top_k=2,
    )
    hit = next(item for item in result.hits if item["chunk_id"] == "hit")
    assert len(hit["text"]) <= 1200
    assert hit["context"] == []  # before is outside pages; after crosses section.
    assert len({item["chunk_id"] for item in result.hits}) == len(result.hits)


def test_query_centered_snippet_is_bounded():
    text = "a" * 900 + "NEEDLE" + "b" * 900
    snippet = bounded_snippet(text, "needle", 1200)
    assert len(snippet) <= 1200
    assert "NEEDLE" in snippet
    assert snippet.startswith("…") and snippet.endswith("…")
