from __future__ import annotations

import logging
import math
import re
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any

import numpy as np

from backend.core.config import AppSettings
from backend.core.request_policy import RequestPolicy
from backend.tools.embedder import BaseEmbedder, OpenAICompatibleEmbedder

logger = logging.getLogger(__name__)
TOKEN = re.compile(r"[A-Za-z0-9_]+|[\u3400-\u9fff]+", re.UNICODE)


def terms(text: str) -> list[str]:
    """Tokenize English words and Chinese characters/bigrams for lexical search."""
    result: list[str] = []
    for raw in TOKEN.findall(text.lower()):
        if re.fullmatch(r"[\u3400-\u9fff]+", raw):
            chars = list(raw)
            result.extend(chars)
            result.extend(raw[i : i + 2] for i in range(len(raw) - 1))
        else:
            result.append(raw)
    return result


@dataclass
class RetrievalIndex:
    chunks: list[dict[str, Any]]
    documents: list[Counter[str]]
    lengths: list[int]
    document_frequency: Counter[str]
    vectors: np.ndarray | None = None
    degraded_reason: str | None = None


@dataclass
class RetrievalDiagnostics:
    rewritten_query: str
    bm25_enabled: bool = True
    vector_enabled: bool = False
    degraded_reason: str | None = None
    bm25_candidates: int = 0
    vector_candidates: int = 0
    final_scores: list[float] = field(default_factory=list)


@dataclass
class RetrievalResult:
    hits: list[tuple[float, dict[str, Any]]]
    diagnostics: RetrievalDiagnostics


class RetrievalCache:
    def __init__(self, maxsize: int = 8):
        self.maxsize = maxsize
        self.data: OrderedDict[tuple[Any, ...], RetrievalIndex] = OrderedDict()
        self.lock = Lock()

    def get(self, key: tuple[Any, ...]) -> RetrievalIndex | None:
        with self.lock:
            value = self.data.get(key)
            if value is not None:
                self.data.move_to_end(key)
            return value

    def put(self, key: tuple[Any, ...], value: RetrievalIndex) -> None:
        with self.lock:
            self.data[key] = value
            self.data.move_to_end(key)
            while len(self.data) > self.maxsize:
                self.data.popitem(last=False)


class AskPaperRetrievalService:
    def __init__(
        self,
        settings: AppSettings,
        embedder: BaseEmbedder | None = None,
        cache: RetrievalCache | None = None,
    ) -> None:
        self.settings = settings
        self.embedder = embedder
        self.cache = cache or RetrievalCache(settings.ask_retrieval_cache_size)

    def _embedder(self) -> BaseEmbedder:
        if self.embedder is None:
            if self.settings.use_mock_embedding:
                raise RuntimeError("mock embedding is intentionally disabled for Ask Paper")
            self.embedder = OpenAICompatibleEmbedder(
                api_key=self.settings.embedding_api_key or "",
                model_name=self.settings.embedding_model,
                base_url=self.settings.embedding_base_url,
                request_policy=RequestPolicy.from_settings(self.settings),
                timeout=(self.settings.request_connect_timeout, self.settings.request_read_timeout),
            )
        return self.embedder

    @staticmethod
    def _build_lexical(chunks: list[dict[str, Any]]) -> RetrievalIndex:
        documents = [Counter(terms(str(c.get("text", "")))) for c in chunks]
        df: Counter[str] = Counter()
        for document in documents:
            df.update(document.keys())
        return RetrievalIndex(chunks, documents, [sum(x.values()) for x in documents], df)

    def _cache_key(self, task_id: str, path: Path, section: str | None) -> tuple[Any, ...]:
        return (
            task_id,
            path.stat().st_mtime_ns,
            section or "*",
            self.settings.embedding_provider,
            self.settings.embedding_model,
        )

    def _index(
        self, task_id: str, path: Path, section: str | None, chunks: list[dict[str, Any]]
    ) -> tuple[RetrievalIndex, str | None]:
        key = self._cache_key(task_id, path, section)
        cached = self.cache.get(key)
        if cached is not None:
            return cached, cached.degraded_reason
        index = self._build_lexical(chunks)
        degraded = None
        if not self.settings.use_mock_embedding and chunks:
            try:
                vectors = self._embedder().embed_texts([str(c.get("text", "")) for c in chunks])
                matrix = np.asarray(vectors, dtype=np.float32)
                norms = np.linalg.norm(matrix, axis=1, keepdims=True)
                norms[norms == 0] = 1
                index.vectors = matrix / norms
            except Exception as exc:
                degraded = f"embedding_unavailable:{type(exc).__name__}"
                index.degraded_reason = degraded
                logger.warning("Ask Paper vector index degraded: %s", type(exc).__name__)
        self.cache.put(key, index)
        return index, degraded

    @staticmethod
    def bm25(index: RetrievalIndex, query: str, limit: int) -> list[tuple[int, float]]:
        query_terms = terms(query)
        if not query_terms or not index.chunks:
            return []
        n = len(index.chunks)
        average = sum(index.lengths) / n if n else 1
        scores: list[tuple[int, float]] = []
        for position, document in enumerate(index.documents):
            score = 0.0
            for term in query_terms:
                frequency = document.get(term, 0)
                if not frequency:
                    continue
                idf = math.log(1 + (n - index.document_frequency[term] + 0.5) / (index.document_frequency[term] + 0.5))
                denominator = frequency + 1.5 * (1 - 0.75 + 0.75 * index.lengths[position] / max(1, average))
                score += idf * frequency * 2.5 / denominator
            if score > 0:
                scores.append((position, score))
        return sorted(scores, key=lambda item: (-item[1], item[0]))[:limit]

    def vector(self, index: RetrievalIndex, query: str, limit: int) -> list[tuple[int, float]]:
        if index.vectors is None:
            return []
        vector = np.asarray(self._embedder().embed_query(query), dtype=np.float32)
        norm = np.linalg.norm(vector)
        if norm == 0:
            return []
        scores = index.vectors @ (vector / norm)
        positions = np.argsort(scores)[::-1][:limit]
        return [(int(i), float(scores[int(i)])) for i in positions]

    def retrieve(
        self,
        task_id: str,
        state_path: str,
        query: str,
        section: str | None = None,
    ) -> RetrievalResult:
        path = Path(state_path)
        import json

        state = json.loads(path.read_text(encoding="utf-8"))
        chunks = [c for c in (state.get("document") or {}).get("chunks", []) if isinstance(c, dict)]
        if section:
            chunks = [c for c in chunks if c.get("section") == section]
        index, degraded = self._index(task_id, path, section, chunks)
        candidate_count = self.settings.ask_candidate_count
        lexical = self.bm25(index, query, candidate_count)
        semantic: list[tuple[int, float]] = []
        if index.vectors is not None:
            try:
                semantic = self.vector(index, query, candidate_count)
            except Exception as exc:
                degraded = f"embedding_query_unavailable:{type(exc).__name__}"
                logger.warning("Ask Paper vector query degraded: %s", type(exc).__name__)
        fused: dict[int, float] = {}
        for ranked in (lexical, semantic):
            for rank, (position, _) in enumerate(ranked, 1):
                fused[position] = fused.get(position, 0) + 1 / (self.settings.ask_rrf_k + rank)
        ordered = sorted(fused.items(), key=lambda item: (-item[1], item[0]))[: self.settings.ask_evidence_count]
        diagnostics = RetrievalDiagnostics(
            rewritten_query=query,
            vector_enabled=index.vectors is not None,
            degraded_reason=degraded,
            bm25_candidates=len(lexical),
            vector_candidates=len(semantic),
            final_scores=[score for _, score in ordered],
        )
        logger.info(
            "Ask Paper retrieval query=%r bm25=%d vector=%d degraded=%s final=%d",
            query[:240], len(lexical), len(semantic), degraded, len(ordered),
        )
        return RetrievalResult([(score, index.chunks[position]) for position, score in ordered], diagnostics)


_default_service: AskPaperRetrievalService | None = None
_default_signature: tuple[Any, ...] | None = None
_default_lock = Lock()


def get_retrieval_service(settings: AppSettings) -> AskPaperRetrievalService:
    """Return the process-local service so its bounded index cache survives questions."""
    global _default_service, _default_signature
    signature = (
        settings.embedding_provider,
        settings.embedding_model,
        settings.embedding_base_url,
        settings.ask_candidate_count,
        settings.ask_evidence_count,
        settings.ask_rrf_k,
        settings.ask_retrieval_cache_size,
    )
    with _default_lock:
        if _default_service is None or signature != _default_signature:
            _default_service = AskPaperRetrievalService(settings)
            _default_signature = signature
        return _default_service
