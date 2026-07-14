from __future__ import annotations

import logging
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from backend.ask_retrieval import (
    AskPaperRetrievalService,
    RetrievalIndex,
    _chunk_overlaps_pages,
    get_retrieval_service,
    terms,
)
from backend.core.config import AppSettings
from backend.retrieval_index import state_content_sha256

logger = logging.getLogger(__name__)

SearchMode = Literal["auto", "bm25"]
ModeUsed = Literal["hybrid", "bm25", "degraded_to_bm25"]
IndexSource = Literal["memory_hit", "disk_hit", "cold_build", "unavailable"]


@dataclass(frozen=True)
class DocumentSearchResult:
    mode_used: ModeUsed
    hits: list[dict[str, Any]]
    candidate_count: int
    elapsed_ms: float
    index_source: IndexSource
    fallback_reason: str | None = None


def _query_positions(text: str, query: str) -> list[int]:
    normalized_text = unicodedata.normalize("NFKC", text).casefold()
    candidates = list(dict.fromkeys(terms(query)))
    positions = [normalized_text.find(term.casefold()) for term in candidates]
    return [position for position in positions if position >= 0]


def bounded_snippet(text: str, query: str, limit: int) -> str:
    """Bound text and, when possible, center the earliest lexical match."""
    text = text.strip()
    if len(text) <= limit:
        return text
    positions = _query_positions(text, query)
    anchor = min(positions) if positions else 0
    start = max(0, anchor - limit // 2)
    end = min(len(text), start + limit)
    if end - start < limit:
        start = max(0, end - limit)
    prefix = "…" if start else ""
    suffix = "…" if end < len(text) else ""
    available = limit - len(prefix) - len(suffix)
    return f"{prefix}{text[start:start + available]}{suffix}"


def _in_scope(
    chunk: dict[str, Any],
    section: str | None,
    page_start: int | None,
    page_end: int | None,
) -> bool:
    return (not section or chunk.get("section") == section) and (
        page_start is None
        or page_end is None
        or _chunk_overlaps_pages(chunk, page_start, page_end)
    )


class DocumentSearchService:
    def __init__(
        self,
        settings: AppSettings,
        retrieval: AskPaperRetrievalService | None = None,
    ) -> None:
        self.settings = settings
        self.retrieval = retrieval or get_retrieval_service(settings)

    def _context(
        self,
        chunks: list[dict[str, Any]],
        original_position: int,
        query: str,
        page_start: int | None,
        page_end: int | None,
    ) -> list[dict[str, Any]]:
        hit = chunks[original_position]
        context: list[dict[str, Any]] = []
        seen: set[str] = {str(hit.get("chunk_id") or "")}
        for relation, position in (
            ("before", original_position - 1),
            ("after", original_position + 1),
        ):
            if position < 0 or position >= len(chunks):
                continue
            adjacent = chunks[position]
            chunk_id = str(adjacent.get("chunk_id") or "")
            if not chunk_id or chunk_id in seen:
                continue
            if adjacent.get("section") != hit.get("section"):
                continue
            if (
                page_start is not None
                and page_end is not None
                and not _chunk_overlaps_pages(adjacent, page_start, page_end)
            ):
                continue
            seen.add(chunk_id)
            context.append(
                {
                    "relation": relation,
                    "chunk_id": chunk_id,
                    "text": bounded_snippet(str(adjacent.get("text") or ""), query, 600),
                    "section": adjacent.get("section"),
                    "page_start": adjacent.get("page_start"),
                    "page_end": adjacent.get("page_end"),
                }
            )
        return context

    def search(
        self,
        task_id: str,
        state_path: str,
        state: dict[str, Any],
        query: str,
        *,
        mode: SearchMode = "auto",
        section: str | None = None,
        page_start: int | None = None,
        page_end: int | None = None,
        top_k: int = 6,
    ) -> DocumentSearchResult:
        started = time.perf_counter()
        chunks = [
            chunk
            for chunk in (state.get("document") or {}).get("chunks", [])
            if isinstance(chunk, dict)
        ]
        positions = [
            position
            for position, chunk in enumerate(chunks)
            if _in_scope(chunk, section, page_start, page_end)
        ]
        scoped_chunks = [chunks[position] for position in positions]
        lexical_index = self.retrieval._build_lexical(scoped_chunks)
        candidate_limit = max(top_k, self.settings.ask_candidate_count)
        lexical = self.retrieval.bm25(
            lexical_index,
            query,
            candidate_limit,
            self.settings.ask_bm25_k1,
            self.settings.ask_bm25_b,
        )

        mode_used: ModeUsed = "bm25"
        index_source: IndexSource = "unavailable"
        fallback_reason: str | None = None
        semantic: list[tuple[int, float]] = []
        full_index: RetrievalIndex | None = None

        vector_configured = (
            mode == "auto"
            and not self.settings.use_mock_embedding
            and bool(self.settings.embedding_api_key)
        )
        if vector_configured and chunks:
            full_index, degraded, memory_hit = self.retrieval._index(
                task_id,
                Path(state_path),
                state_content_sha256(state),
                chunks,
            )
            if degraded or full_index.vectors is None:
                mode_used = "degraded_to_bm25"
                fallback_reason = "index_build_unavailable"
            else:
                index_source = (
                    "memory_hit"
                    if memory_hit
                    else "disk_hit"
                    if full_index.persistent_cache_hit
                    else "cold_build"
                )
                scoped_index = self.retrieval._slice(full_index, positions)
                try:
                    semantic = self.retrieval.filter_vectors(
                        self.retrieval.vector(scoped_index, query, candidate_limit)
                    )
                    mode_used = "hybrid"
                except Exception as exc:
                    mode_used = "degraded_to_bm25"
                    fallback_reason = "query_embedding_unavailable"
                    semantic = []
                    logger.warning(
                        "Document search vector query degraded: %s", type(exc).__name__
                    )

        if mode_used == "hybrid":
            fused: dict[int, float] = {}
            for ranked in (lexical, semantic):
                for rank, (position, _) in enumerate(ranked, 1):
                    fused[position] = fused.get(position, 0.0) + 1 / (
                        self.settings.ask_rrf_k + rank
                    )
            ordered = sorted(fused.items(), key=lambda item: (-item[1], item[0]))
            candidate_count = len(fused)
        else:
            ordered = [(position, score) for position, score in lexical]
            candidate_count = len(lexical)

        bm25_map = {position: score for position, score in lexical}
        vector_map = {position: score for position, score in semantic}
        hits: list[dict[str, Any]] = []
        for rank, (scoped_position, score) in enumerate(ordered[:top_k], 1):
            chunk = scoped_chunks[scoped_position]
            original_position = positions[scoped_position]
            bm25_score = bm25_map.get(scoped_position)
            vector_score = vector_map.get(scoped_position) if mode_used == "hybrid" else None
            sources = [
                source
                for source, present in (
                    ("bm25", bm25_score is not None),
                    ("vector", vector_score is not None),
                )
                if present
            ]
            hits.append(
                {
                    "rank": rank,
                    "chunk_id": str(chunk.get("chunk_id") or f"chunk-{original_position}"),
                    "text": bounded_snippet(str(chunk.get("text") or ""), query, 1200),
                    "section": chunk.get("section"),
                    "page_start": chunk.get("page_start"),
                    "page_end": chunk.get("page_end"),
                    "sources": sources,
                    "bm25_score": bm25_score,
                    "vector_score": vector_score,
                    "hybrid_score": score if mode_used == "hybrid" else None,
                    "context": self._context(
                        chunks,
                        original_position,
                        query,
                        page_start,
                        page_end,
                    ),
                }
            )

        return DocumentSearchResult(
            mode_used=mode_used,
            hits=hits,
            candidate_count=candidate_count,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            index_source=index_source,
            fallback_reason=fallback_reason,
        )
