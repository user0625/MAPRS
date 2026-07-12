import json

import pytest

from backend.reranker import OpenAICompatibleReranker


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        return json.dumps(self.payload).encode()


def test_qwen_rerank_request_and_original_order(monkeypatch):
    captured = {}

    def fake_open(request, timeout):
        captured["url"] = request.full_url
        captured["authorization"] = request.headers["Authorization"]
        captured["payload"] = json.loads(request.data)
        captured["timeout"] = timeout
        return FakeResponse({
            "results": [
                {"index": 1, "relevance_score": 0.9},
                {"index": 0, "relevance_score": 0.2},
            ]
        })

    monkeypatch.setattr("backend.reranker.urlopen", fake_open)
    reranker = OpenAICompatibleReranker(
        "secret", "qwen3-rerank", "https://workspace.example/compatible-api/v1/"
    )
    assert reranker.score("question", ["first", "second"], 1.0) == [0.2, 0.9]
    assert captured["url"] == "https://workspace.example/compatible-api/v1/reranks"
    assert captured["authorization"] == "Bearer secret"
    assert captured["payload"]["documents"] == ["first", "second"]
    assert captured["payload"]["top_n"] == 2
    assert captured["timeout"] == 1.0


@pytest.mark.parametrize("payload", [
    {},
    {"results": [{"index": 0, "relevance_score": 0.5}]},
    {"results": [{"index": 0, "relevance_score": 0.5}, {"index": 0, "relevance_score": 0.4}]},
])
def test_qwen_rerank_rejects_malformed_responses(monkeypatch, payload):
    monkeypatch.setattr("backend.reranker.urlopen", lambda request, timeout: FakeResponse(payload))
    reranker = OpenAICompatibleReranker("secret", "qwen3-rerank", "https://example/v1")
    with pytest.raises(ValueError):
        reranker.score("question", ["first", "second"], 1.0)


def test_reranker_does_not_call_api_for_empty_passages(monkeypatch):
    monkeypatch.setattr(
        "backend.reranker.urlopen",
        lambda *args, **kwargs: pytest.fail("API should not be called"),
    )
    reranker = OpenAICompatibleReranker("secret", "qwen3-rerank", "https://example/v1")
    assert reranker.score("question", [], 1.0) == []
