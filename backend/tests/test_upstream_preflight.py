import socket
import ssl
from urllib.error import HTTPError

import pytest

from backend.core.config import AppSettings
from backend.evaluation.upstream_preflight import classify_preflight_failure, run_preflight
from backend.tools.embedder import BaseEmbedder


class Embedder(BaseEmbedder):
    model_name = "fixture"

    def __init__(self, error=None, result=None):
        self.error = error
        self.result = result or [1.0, 0.0]

    def embed_text(self, text):
        if self.error:
            raise self.error
        return self.result


class Reranker:
    model_name = "fixture"

    def __init__(self, error=None, result=None):
        self.error = error
        self.result = result or [0.9, 0.1]

    def score(self, query, passages, timeout):
        if self.error:
            raise self.error
        return self.result


def settings():
    return AppSettings(_env_file=None, ask_reranker_timeout=0.1)


@pytest.mark.parametrize(("error", "category"), [
    (HTTPError("https://example", 401, "secret", {}, None), "authentication"),
    (HTTPError("https://example", 404, "secret", {}, None), "endpoint_or_model"),
    (HTTPError("https://example", 429, "secret", {}, None), "rate_limit"),
    (HTTPError("https://example", 503, "secret", {}, None), "upstream_5xx"),
    (socket.gaierror("secret host"), "dns"),
    (ssl.SSLError("secret cert"), "tls"),
    (TimeoutError("secret timeout"), "timeout"),
    (ValueError("secret malformed body"), "response_schema"),
])
def test_preflight_failure_categories_are_safe(error, category):
    assert classify_preflight_failure(error) == category


def test_preflight_success_and_redacted_failure_payload():
    success = run_preflight(settings(), embedder=Embedder(), reranker=Reranker())
    assert success["ok"] is True
    failed = run_preflight(
        settings(), embedder=Embedder(error=ValueError("api-key-secret")), reranker=Reranker()
    )
    assert failed["ok"] is False
    assert "api-key-secret" not in str(failed)
    assert failed["checks"][0]["configuration"] == [
        "EMBEDDING_API_KEY", "EMBEDDING_BASE_URL", "EMBEDDING_MODEL"
    ]
