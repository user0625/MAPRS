from __future__ import annotations

import json
import math
import socket
import ssl
import time
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlsplit

from backend.core.config import AppSettings, get_settings
from backend.core.request_policy import (
    NetworkTimeoutError,
    RateLimitError,
    RequestPolicy,
)
from backend.reranker import BaseReranker, OpenAICompatibleReranker
from backend.tools.embedder import BaseEmbedder, OpenAICompatibleEmbedder


@dataclass(frozen=True)
class PreflightCheck:
    service: str
    ok: bool
    category: str
    elapsed_ms: float
    request_count: int
    configuration: list[str]


def _status(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    value = (
        getattr(exc, "status_code", None)
        or getattr(exc, "code", None)
        or getattr(response, "status_code", None)
        or getattr(response, "status", None)
    )
    return value if isinstance(value, int) else None


def _exception_chain(exc: BaseException) -> list[BaseException]:
    result: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in result:
        result.append(current)
        reason = getattr(current, "reason", None)
        current = reason if isinstance(reason, BaseException) else current.__cause__
    return result


def classify_preflight_failure(exc: BaseException) -> str:
    chain = _exception_chain(exc)
    statuses = [_status(item) for item in chain]
    if any(status in (401, 403) for status in statuses):
        return "authentication"
    if 404 in statuses:
        return "endpoint_or_model"
    if 429 in statuses or any(isinstance(item, RateLimitError) for item in chain):
        return "rate_limit"
    if any(isinstance(status, int) and status >= 500 for status in statuses):
        return "upstream_5xx"
    if any(isinstance(item, (TimeoutError, NetworkTimeoutError)) for item in chain):
        return "timeout"
    if any(isinstance(item, socket.gaierror) for item in chain):
        return "dns"
    if any(isinstance(item, (ssl.SSLError, ssl.CertificateError)) for item in chain):
        return "tls"
    if any(isinstance(item, (ValueError, TypeError, KeyError, json.JSONDecodeError)) for item in chain):
        return "response_schema"
    return "network_or_protocol"


def _url_has_path(value: str | None, expected: str) -> bool:
    if not value:
        return False
    parsed = urlsplit(value)
    return parsed.scheme == "https" and bool(parsed.netloc) and expected in parsed.path.rstrip("/")


def _check(
    service: str,
    configuration: list[str],
    operation: Any,
    validate: Any,
    request_counter: Any | None = None,
) -> PreflightCheck:
    started = time.perf_counter()
    starting_count = request_counter() if request_counter else 0
    try:
        result = operation()
        validate(result)
    except Exception as exc:
        return PreflightCheck(
            service=service,
            ok=False,
            category=classify_preflight_failure(exc),
            elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
            request_count=max(1, request_counter() - starting_count) if request_counter else 1,
            configuration=configuration,
        )
    return PreflightCheck(
        service=service,
        ok=True,
        category="ok",
        elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
        request_count=max(1, request_counter() - starting_count) if request_counter else 1,
        configuration=[],
    )


def run_preflight(
    settings: AppSettings | None = None,
    *,
    embedder: BaseEmbedder | None = None,
    reranker: BaseReranker | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    checks: list[PreflightCheck] = []
    embedding_config = ["EMBEDDING_API_KEY", "EMBEDDING_BASE_URL", "EMBEDDING_MODEL"]
    reranker_config = [
        "ASK_RERANKER_API_KEY",
        "ASK_RERANKER_BASE_URL",
        "ASK_RERANKER_MODEL",
    ]

    if embedder is None and (
        settings.embedding_provider != "openai_compatible"
        or not settings.embedding_api_key
        or not settings.embedding_model
        or not _url_has_path(settings.embedding_base_url, "/compatible-mode/v1")
    ):
        checks.append(PreflightCheck("embedding", False, "configuration", 0.0, 0, embedding_config))
    else:
        embedding = embedder or OpenAICompatibleEmbedder(
            api_key=settings.embedding_api_key or "",
            model_name=settings.embedding_model,
            base_url=settings.embedding_base_url,
            batch_size=1,
            request_policy=RequestPolicy.from_settings(settings),
            timeout=(settings.request_connect_timeout, settings.request_read_timeout),
        )

        def validate_embedding(value: object) -> None:
            if not isinstance(value, list) or not value:
                raise ValueError("invalid embedding response")
            if any(not isinstance(item, (int, float)) or not math.isfinite(float(item)) for item in value):
                raise ValueError("invalid embedding vector")

        checks.append(_check(
            "embedding", embedding_config,
            lambda: embedding.embed_text("Synthetic scientific abstract about robust evaluation."),
            validate_embedding,
            lambda: getattr(embedding, "request_count", 0),
        ))

    if reranker is None and (
        not settings.ask_reranker_api_key
        or not settings.ask_reranker_model
        or not _url_has_path(settings.ask_reranker_base_url, "/compatible-api/v1")
    ):
        checks.append(PreflightCheck("reranker", False, "configuration", 0.0, 0, reranker_config))
    else:
        ranker = reranker or OpenAICompatibleReranker(
            settings.ask_reranker_api_key or "",
            settings.ask_reranker_model,
            settings.ask_reranker_base_url,
            request_policy=RequestPolicy.from_settings(settings),
        )

        def validate_reranker(value: object) -> None:
            if not isinstance(value, list) or len(value) != 2:
                raise ValueError("invalid reranker response")
            if any(not isinstance(item, (int, float)) or not math.isfinite(float(item)) for item in value):
                raise ValueError("invalid reranker scores")

        checks.append(_check(
            "reranker", reranker_config,
            lambda: ranker.score(
                "Which passage directly answers the scientific question?",
                ["The method uses controlled ablation experiments.", "This passage is unrelated."],
                settings.ask_reranker_timeout,
            ),
            validate_reranker,
            lambda: getattr(ranker, "request_count", 0),
        ))

    return {
        "schema_version": "upstream-preflight-v1",
        "ok": all(check.ok for check in checks),
        "checks": [asdict(check) for check in checks],
    }


def main() -> int:
    result = run_preflight()
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
