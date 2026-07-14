from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from backend.core.request_policy import RequestPolicy


logger = logging.getLogger(__name__)
RERANK_INSTRUCTION = (
    "For scientific-paper question answering, assign a high relevance score only to a "
    "passage that contains evidence directly answering the question. Background, topical "
    "overlap, and keyword matches without an answer must receive a low score."
)


class BaseReranker(ABC):
    """Provider-neutral passage scorer. Scores must align with the input passages."""

    model_name: str

    @abstractmethod
    def score(self, query: str, passages: list[str], timeout: float) -> list[float]:
        raise NotImplementedError


class OpenAICompatibleReranker(BaseReranker):
    """Adapter for the OpenAI/Cohere-style ``POST /reranks`` protocol.

    Qwen3-rerank returns results in relevance order, so scores are restored to
    the original passage order required by :class:`BaseReranker`.
    """

    def __init__(
        self,
        api_key: str,
        model_name: str,
        base_url: str | None = None,
        request_policy: RequestPolicy | None = None,
    ) -> None:
        self.api_key = api_key
        self.model_name = model_name
        self.base_url = (base_url or "").strip()
        self.request_policy = request_policy
        self.request_count = 0

    def _endpoint(self) -> str:
        if not self.base_url:
            raise ValueError("reranker base URL is required")
        parts = urlsplit(self.base_url)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError("reranker base URL must be HTTP(S)")
        path = parts.path.rstrip("/")
        if not path.endswith("/reranks"):
            path += "/reranks"
        return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))

    def score(self, query: str, passages: list[str], timeout: float) -> list[float]:
        if not passages:
            return []
        payload = json.dumps(
            {
                "model": self.model_name,
                "query": query,
                "documents": passages,
                "top_n": len(passages),
                "instruct": RERANK_INSTRUCTION,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(
            self._endpoint(),
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        started = time.perf_counter()
        starting_count = self.request_count
        try:
            def operation() -> object:
                self.request_count += 1
                with urlopen(request, timeout=timeout) as response:  # noqa: S310
                    return json.loads(response.read().decode("utf-8"))

            body = self.request_policy.call(operation) if self.request_policy else operation()
        except Exception as exc:
            logger.warning(
                "Reranker request failed category=%s elapsed_ms=%.2f request_count=%d",
                type(exc).__name__,
                (time.perf_counter() - started) * 1000,
                self.request_count - starting_count,
            )
            raise
        logger.info(
            "Reranker request completed elapsed_ms=%.2f request_count=%d",
            (time.perf_counter() - started) * 1000,
            self.request_count - starting_count,
        )
        results = body.get("results") if isinstance(body, dict) else None
        if not isinstance(results, list):
            raise ValueError("reranker response has no results")
        scores: list[float | None] = [None] * len(passages)
        for result in results:
            if not isinstance(result, dict):
                raise ValueError("reranker result is invalid")
            index = result.get("index")
            value = result.get("relevance_score")
            if not isinstance(index, int) or not 0 <= index < len(scores) or scores[index] is not None:
                raise ValueError("reranker result index is invalid")
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError("reranker relevance score is invalid")
            scores[index] = float(value)
        if any(value is None for value in scores):
            raise ValueError("reranker returned an incomplete score set")
        return [value for value in scores if value is not None]
