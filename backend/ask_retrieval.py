from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import unicodedata
import time
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any

import numpy as np

from backend.core.config import AppSettings
from backend.core.request_policy import RequestPolicy
from backend.tools.embedder import BaseEmbedder, OpenAICompatibleEmbedder
from backend.reranker import BaseReranker, OpenAICompatibleReranker
from backend.retrieval_index import (
    INDEX_SCHEMA,
    chunks_sha256,
    load_vectors,
    save_vectors,
    state_content_sha256,
)

logger = logging.getLogger(__name__)
TOKEN = re.compile(r"[A-Za-z0-9_]+|[\u3400-\u9fff]+", re.UNICODE)
ENGLISH_STOPWORDS = frozenset({"a", "an", "and", "are", "for", "in", "is", "of", "on", "or", "the", "to", "was", "were"})
CHINESE_STOPWORDS = frozenset({"的", "了", "和", "是", "在", "与", "及", "中"})


def _chunk_overlaps_pages(
    chunk: dict[str, Any], page_start: int, page_end: int
) -> bool:
    raw_start = chunk.get("page_start")
    raw_end = chunk.get("page_end")
    chunk_start = raw_start if isinstance(raw_start, int) else raw_end
    chunk_end = raw_end if isinstance(raw_end, int) else raw_start
    if not isinstance(chunk_start, int) or not isinstance(chunk_end, int):
        return False
    return chunk_start <= page_end and chunk_end >= page_start


def terms(text: str) -> list[str]:
    """Tokenize English words and Chinese characters/bigrams for lexical search."""
    result: list[str] = []
    normalized = unicodedata.normalize("NFKC", text).lower().replace("’", "'")
    for raw in TOKEN.findall(normalized):
        if re.fullmatch(r"[\u3400-\u9fff]+", raw):
            chars = [char for char in raw if char not in CHINESE_STOPWORDS]
            result.extend(chars)
            result.extend(chars[i] + chars[i + 1] for i in range(len(chars) - 1))
        elif raw not in ENGLISH_STOPWORDS:
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
    state_sha256: str | None = None
    chunks_sha256: str | None = None
    index_build_ms: float = 0.0
    index_load_ms: float = 0.0
    persistent_cache_hit: bool = False
    cold_build_failed: bool = False
    cache_digest: str | None = None


@dataclass
class RetrievalDiagnostics:
    rewritten_query: str
    bm25_enabled: bool = True
    vector_enabled: bool = False
    degraded_reason: str | None = None
    bm25_candidates: int = 0
    vector_candidates: int = 0
    vector_candidates_raw: int = 0
    vector_candidates_filtered: int = 0
    vector_candidates_removed: int = 0
    rrf_candidates: int = 0
    candidate_limit: int = 0
    evidence_limit: int = 0
    vector_min_similarity: float | None = None
    final_scores: list[float] = field(default_factory=list)
    candidate_scores: list[dict[str, Any]] = field(default_factory=list)
    bm25_scores_raw: list[dict[str, Any]] = field(default_factory=list)
    vector_scores_raw: list[dict[str, Any]] = field(default_factory=list)
    reranker_mode: str = "disabled"
    reranker_latency_ms: float | None = None
    reranker_top_score: float | None = None
    reranker_applied: bool = False
    reranker_rank_changes: int = 0
    answerable: bool = True
    evidence_threshold: float | None = None
    answerability_threshold: float | None = None
    calibration_version: str = "uncalibrated"
    index_schema: str = INDEX_SCHEMA
    index_build_ms: float = 0.0
    index_load_ms: float = 0.0
    index_cache_hit: bool = False
    index_memory_cache_hit: bool = False
    index_cold_build_failed: bool = False
    index_cache_digest: str | None = None


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
        filter_vector_candidates: bool = True,
        reranker: BaseReranker | None = None,
    ) -> None:
        self.settings = settings
        self.embedder = embedder
        self.cache = cache or RetrievalCache(settings.ask_retrieval_cache_size)
        self.filter_vector_candidates = filter_vector_candidates
        self.reranker = reranker

    def _reranker(self) -> BaseReranker:
        if self.reranker is None:
            if not self.settings.ask_reranker_model or not self.settings.ask_reranker_api_key:
                raise RuntimeError("reranker is not configured")
            self.reranker = OpenAICompatibleReranker(
                self.settings.ask_reranker_api_key,
                self.settings.ask_reranker_model,
                self.settings.ask_reranker_base_url,
                request_policy=RequestPolicy.from_settings(self.settings),
            )
        return self.reranker

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

    def _cache_key(
        self,
        task_id: str,
        path: Path,
        state_sha256: str,
    ) -> tuple[Any, ...]:
        return (
            task_id,
            path.stat().st_mtime_ns,
            state_sha256,
            INDEX_SCHEMA,
            self.settings.embedding_provider,
            self.settings.embedding_model,
        )

    def _index(
        self,
        task_id: str,
        path: Path,
        state_sha256: str,
        chunks: list[dict[str, Any]],
    ) -> tuple[RetrievalIndex, str | None, bool]:
        key = self._cache_key(task_id, path, state_sha256)
        cached = self.cache.get(key)
        if cached is not None:
            return cached, cached.degraded_reason, True
        index = self._build_lexical(chunks)
        index.state_sha256 = state_sha256
        index.chunks_sha256 = chunks_sha256(chunks)
        degraded = None
        if not self.settings.use_mock_embedding and chunks:
            chunk_ids = [str(chunk.get("chunk_id") or "") for chunk in chunks]
            try:
                embedder = self._embedder()
                persisted = load_vectors(
                    self.settings,
                    task_id,
                    state_sha256=state_sha256,
                    chunk_sha256=index.chunks_sha256,
                    chunk_ids=chunk_ids,
                    embedding_model=embedder.model_name,
                )
                if persisted is not None:
                    index.vectors = persisted.vectors
                    index.index_load_ms = persisted.load_ms
                    index.persistent_cache_hit = True
                    index.cache_digest = persisted.cache_digest[:16]
                else:
                    started = time.perf_counter()
                    vectors = embedder.embed_texts([str(c.get("text", "")) for c in chunks])
                    matrix = np.asarray(vectors, dtype=np.float32)
                    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
                    if not np.isfinite(matrix).all():
                        raise ValueError("embedding vectors must be finite")
                    norms[norms == 0] = 1
                    index.vectors = matrix / norms
                    index.index_build_ms = (time.perf_counter() - started) * 1000
                    metadata = save_vectors(
                        self.settings,
                        task_id,
                        state_sha256=state_sha256,
                        chunk_sha256=index.chunks_sha256,
                        chunk_ids=chunk_ids,
                        vectors=index.vectors,
                        embedding_model=embedder.model_name,
                    )
                    index.cache_digest = str(metadata["cache_digest"])
            except Exception as exc:
                degraded = f"embedding_unavailable:{type(exc).__name__}"
                index.degraded_reason = degraded
                index.cold_build_failed = True
                logger.warning("Ask Paper vector index degraded: %s", type(exc).__name__)
        self.cache.put(key, index)
        return index, degraded, False

    @staticmethod
    def _slice(index: RetrievalIndex, positions: list[int]) -> RetrievalIndex:
        vectors = index.vectors[positions] if index.vectors is not None else None
        sliced = AskPaperRetrievalService._build_lexical([index.chunks[position] for position in positions])
        sliced.vectors = vectors
        sliced.degraded_reason = index.degraded_reason
        sliced.state_sha256 = index.state_sha256
        sliced.chunks_sha256 = index.chunks_sha256
        sliced.index_build_ms = index.index_build_ms
        sliced.index_load_ms = index.index_load_ms
        sliced.persistent_cache_hit = index.persistent_cache_hit
        sliced.cold_build_failed = index.cold_build_failed
        sliced.cache_digest = index.cache_digest
        return sliced

    @staticmethod
    def bm25(
        index: RetrievalIndex,
        query: str,
        limit: int,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> list[tuple[int, float]]:
        if k1 <= 0 or not 0 <= b <= 1:
            raise ValueError("BM25 requires k1 > 0 and b in [0, 1]")
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
                denominator = frequency + k1 * (1 - b + b * index.lengths[position] / max(1, average))
                score += idf * frequency * (k1 + 1) / denominator
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
        # Python's stable sort plus the explicit position key makes equal scores
        # deterministic. np.argsort(...)[::-1] reverses equal-score positions.
        ranked = sorted(enumerate(scores), key=lambda item: (-float(item[1]), item[0]))[:limit]
        return [(position, float(score)) for position, score in ranked]

    def filter_vectors(self, candidates: list[tuple[int, float]]) -> list[tuple[int, float]]:
        """Discard unusable and weak semantic candidates before rank-only fusion."""
        threshold = self.settings.ask_vector_min_similarity
        return [
            (position, score)
            for position, score in candidates
            if math.isfinite(score) and score > 0.0 and score >= threshold
        ]

    def retrieve(
        self,
        task_id: str,
        state_path: str,
        query: str,
        section: str | None = None,
        page_start: int | None = None,
        page_end: int | None = None,
    ) -> RetrievalResult:
        path = Path(state_path)
        state_bytes = path.read_bytes()
        state = json.loads(state_bytes)
        chunks = [c for c in (state.get("document") or {}).get("chunks", []) if isinstance(c, dict)]
        full_index, degraded, memory_hit = self._index(
            task_id,
            path,
            state_content_sha256(state),
            chunks,
        )
        # Build/load timings belong to the request that actually performed the
        # action.  The cached index keeps its provenance, but a process-local
        # memory hit must not report the original cost again.
        request_index_build_ms = 0.0 if memory_hit else full_index.index_build_ms
        request_index_load_ms = 0.0 if memory_hit else full_index.index_load_ms
        positions = [
            position
            for position, chunk in enumerate(chunks)
            if (not section or chunk.get("section") == section)
            and (
                page_start is None
                or page_end is None
                or _chunk_overlaps_pages(chunk, page_start, page_end)
            )
        ]
        index = self._slice(full_index, positions)
        candidate_count = self.settings.ask_candidate_count
        lexical = self.bm25(
            index, query, candidate_count, self.settings.ask_bm25_k1, self.settings.ask_bm25_b
        )
        lexical = [
            (position, score) for position, score in lexical
            if score >= self.settings.ask_bm25_min_score
        ]
        semantic_raw: list[tuple[int, float]] = []
        if index.vectors is not None:
            try:
                semantic_raw = self.vector(index, query, candidate_count)
            except Exception as exc:
                degraded = f"embedding_query_unavailable:{type(exc).__name__}"
                logger.warning("Ask Paper vector query degraded: %s", type(exc).__name__)
        semantic = self.filter_vectors(semantic_raw) if self.filter_vector_candidates else semantic_raw
        fused: dict[int, float] = {}
        for ranked in (lexical, semantic):
            for rank, (position, _) in enumerate(ranked, 1):
                fused[position] = fused.get(position, 0) + 1 / (self.settings.ask_rrf_k + rank)
        # Keep the union at candidate depth. Vector filtering only removes the
        # vector contribution; a BM25 hit remains in the union.
        ordered_candidates = sorted(fused.items(), key=lambda item: (-item[1], item[0]))[:candidate_count]
        bm25_map = {position: (rank, score) for rank, (position, score) in enumerate(lexical, 1)}
        vector_map = {position: (rank, score) for rank, (position, score) in enumerate(semantic_raw, 1)}
        reranker_scores: list[float] | None = None
        reranker_latency_ms: float | None = None
        mode = self.settings.ask_reranker_mode
        if mode != "disabled" and ordered_candidates:
            import time
            started = time.perf_counter()
            try:
                reranker_scores = self._reranker().score(
                    query,
                    [str(index.chunks[position].get("text", "")) for position, _ in ordered_candidates],
                    self.settings.ask_reranker_timeout,
                )
                if len(reranker_scores) != len(ordered_candidates):
                    raise ValueError("invalid reranker score count")
                if any(not math.isfinite(score) for score in reranker_scores):
                    raise ValueError("non-finite reranker score")
            except Exception as exc:
                degraded = f"reranker_unavailable:{type(exc).__name__}"
                reranker_scores = None
                logger.warning("Ask Paper reranker degraded: %s", type(exc).__name__)
            reranker_latency_ms = (time.perf_counter() - started) * 1000
        ranked_candidates = ordered_candidates
        reranked_candidate_order = ordered_candidates
        if reranker_scores is not None:
            reranked_candidate_order = [pair for _, pair in sorted(
                enumerate(ordered_candidates), key=lambda item: (-reranker_scores[item[0]], item[0])
            )]
        if mode == "enabled" and reranker_scores is not None:
            ranked_candidates = reranked_candidate_order
        reranker_rank_changes = sum(
            original[0] != reranked[0]
            for original, reranked in zip(ordered_candidates, reranked_candidate_order)
        )
        score_by_position = (
            {position: reranker_scores[i] for i, (position, _) in enumerate(ordered_candidates)}
            if reranker_scores is not None else {}
        )
        top_score = max(reranker_scores) if reranker_scores else None
        answerable = not (
            mode == "enabled" and reranker_scores is not None
            and (top_score or 0.0) < self.settings.ask_answerability_threshold
        )
        if not answerable:
            final = []
        else:
            final = [pair for pair in ranked_candidates if (
                mode != "enabled" or reranker_scores is None
                or score_by_position[pair[0]] >= self.settings.ask_evidence_threshold
            )][: self.settings.ask_evidence_count]
        candidate_scores = []
        for position, hybrid_score in ordered_candidates:
            bm = bm25_map.get(position)
            vec = vector_map.get(position)
            candidate_scores.append({
                "chunk_id": index.chunks[position].get("chunk_id"),
                "bm25_score": bm[1] if bm else None, "bm25_rank": bm[0] if bm else None,
                "vector_score": vec[1] if vec else None, "vector_rank": vec[0] if vec else None,
                "hybrid_score": hybrid_score, "reranker_score": score_by_position.get(position),
                "sources": [name for name, value in (("bm25", bm), ("vector", vec)) if value],
            })
        diagnostics = RetrievalDiagnostics(
            rewritten_query=query,
            vector_enabled=index.vectors is not None
            and not (degraded or "").startswith("embedding_query_unavailable:"),
            degraded_reason=degraded,
            bm25_candidates=len(lexical),
            vector_candidates=len(semantic),
            vector_candidates_raw=len(semantic_raw),
            vector_candidates_filtered=len(semantic),
            vector_candidates_removed=len(semantic_raw) - len(semantic),
            rrf_candidates=len(fused),
            candidate_limit=candidate_count,
            evidence_limit=self.settings.ask_evidence_count,
            vector_min_similarity=(
                self.settings.ask_vector_min_similarity if self.filter_vector_candidates else None
            ),
            final_scores=[score_by_position.get(position, score) for position, score in final],
            candidate_scores=candidate_scores, reranker_mode=mode,
            bm25_scores_raw=[
                {
                    "chunk_id": index.chunks[position].get("chunk_id"),
                    "score": score,
                    "rank": rank,
                }
                for rank, (position, score) in enumerate(lexical, 1)
            ],
            vector_scores_raw=[
                {
                    "chunk_id": index.chunks[position].get("chunk_id"),
                    "score": score,
                    "rank": rank,
                }
                for rank, (position, score) in enumerate(semantic_raw, 1)
            ],
            reranker_latency_ms=reranker_latency_ms, reranker_top_score=top_score,
            reranker_applied=mode == "enabled" and reranker_scores is not None,
            reranker_rank_changes=reranker_rank_changes,
            answerable=answerable, evidence_threshold=self.settings.ask_evidence_threshold,
            answerability_threshold=self.settings.ask_answerability_threshold,
            calibration_version=self.settings.ask_calibration_version,
            index_build_ms=request_index_build_ms,
            index_load_ms=request_index_load_ms,
            index_cache_hit=index.persistent_cache_hit,
            index_memory_cache_hit=memory_hit,
            index_cold_build_failed=index.cold_build_failed,
            index_cache_digest=index.cache_digest,
        )
        logger.info(
            "Ask Paper retrieval query_sha256=%s bm25=%d vector=%d/%d removed=%d rrf=%d "
            "reranker_mode=%s reranker_ms=%s top_score=%s rank_changes=%d degraded=%s returned=%d",
            hashlib.sha256(query.encode("utf-8")).hexdigest()[:12],
            len(lexical), len(semantic), len(semantic_raw), len(semantic_raw) - len(semantic),
            len(fused), mode,
            f"{reranker_latency_ms:.2f}" if reranker_latency_ms is not None else "none",
            f"{top_score:.4f}" if top_score is not None else "none",
            reranker_rank_changes, degraded, len(final),
        )
        return RetrievalResult([
            (score_by_position.get(position, score), index.chunks[position]) for position, score in final
        ], diagnostics)


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
        settings.ask_bm25_k1,
        settings.ask_bm25_b,
        settings.ask_bm25_min_score,
        settings.ask_rrf_k,
        settings.ask_vector_min_similarity,
        settings.ask_retrieval_cache_size,
        settings.ask_index_dir,
        settings.ask_reranker_mode,
        settings.ask_reranker_provider,
        settings.ask_reranker_model,
        settings.ask_reranker_timeout,
        settings.ask_evidence_threshold,
        settings.ask_answerability_threshold,
        settings.ask_calibration_version,
    )
    with _default_lock:
        if _default_service is None or signature != _default_signature:
            _default_service = AskPaperRetrievalService(settings)
            _default_signature = signature
        return _default_service


def prebuild_retrieval_index(
    task_id: str,
    state_path: str | Path,
    settings: AppSettings,
    *,
    embedder: BaseEmbedder | None = None,
) -> dict[str, Any]:
    """Best-effort persistent full-paper vector build with public-safe diagnostics."""
    if not settings.ask_index_prebuild_enabled:
        return {
            "schema_version": INDEX_SCHEMA,
            "status": "disabled",
            "embedding_model": settings.embedding_model,
        }
    if settings.use_mock_embedding:
        return {
            "schema_version": INDEX_SCHEMA,
            "status": "not_applicable",
            "embedding_model": settings.embedding_model,
        }
    path = Path(state_path)
    started = time.perf_counter()
    try:
        state_bytes = path.read_bytes()
        state = json.loads(state_bytes)
        chunks = [
            chunk
            for chunk in (state.get("document") or {}).get("chunks", [])
            if isinstance(chunk, dict)
        ]
        if not chunks:
            raise ValueError("state contains no chunks")
        service = AskPaperRetrievalService(settings, embedder=embedder)
        index, degraded, _ = service._index(
            task_id, path, state_content_sha256(state), chunks
        )
        metadata: dict[str, Any] = {
            "schema_version": INDEX_SCHEMA,
            "status": "degraded" if degraded or index.vectors is None else "ready",
            "embedding_provider": settings.embedding_provider,
            "embedding_model": settings.embedding_model,
            "chunk_count": len(chunks),
            "build_ms": round(index.index_build_ms, 3),
            "load_ms": round(index.index_load_ms, 3),
            "cache_hit": index.persistent_cache_hit,
            "cache_digest": index.cache_digest,
            "cold_build_failed": index.cold_build_failed,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        }
        if degraded:
            metadata["degraded_reason"] = degraded
        return metadata
    except Exception as exc:
        logger.warning("Ask Paper index prebuild degraded: %s", type(exc).__name__)
        return {
            "schema_version": INDEX_SCHEMA,
            "status": "degraded",
            "embedding_provider": settings.embedding_provider,
            "embedding_model": settings.embedding_model,
            "build_ms": round((time.perf_counter() - started) * 1000, 3),
            "cache_hit": False,
            "cold_build_failed": True,
            "degraded_reason": f"index_prebuild_unavailable:{type(exc).__name__}",
        }
