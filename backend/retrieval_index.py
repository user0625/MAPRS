from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from backend.core.config import AppSettings


INDEX_SCHEMA = "ask-retrieval-index-v1"


@dataclass(frozen=True)
class PersistedVectors:
    vectors: np.ndarray
    load_ms: float
    cache_digest: str


def content_sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def state_content_sha256(state: dict[str, Any]) -> str:
    """Hash state content while excluding the index's own safe status metadata."""
    normalized = dict(state)
    metadata = normalized.get("metadata")
    if isinstance(metadata, dict):
        metadata = dict(metadata)
        metadata.pop("ask_retrieval_index", None)
        normalized["metadata"] = metadata
    encoded = json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return content_sha256(encoded.encode("utf-8"))


def chunks_sha256(chunks: list[dict[str, Any]]) -> str:
    safe = [
        {
            "chunk_id": str(chunk.get("chunk_id") or ""),
            "text_sha256": content_sha256(str(chunk.get("text") or "").encode("utf-8")),
            "section": chunk.get("section"),
            "page_start": chunk.get("page_start"),
            "page_end": chunk.get("page_end"),
        }
        for chunk in chunks
    ]
    encoded = json.dumps(safe, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return content_sha256(encoded.encode("utf-8"))


def index_path(settings: AppSettings, task_id: str) -> Path:
    digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()
    return settings.resolve_path(settings.ask_index_dir) / f"{digest}.json"


def cache_digest(state_sha256: str, chunk_sha256: str, provider: str, model: str) -> str:
    value = f"{INDEX_SCHEMA}:{state_sha256}:{chunk_sha256}:{provider}:{model}"
    return content_sha256(value.encode("utf-8"))


def load_vectors(
    settings: AppSettings,
    task_id: str,
    *,
    state_sha256: str,
    chunk_sha256: str,
    chunk_ids: list[str],
    embedding_model: str | None = None,
) -> PersistedVectors | None:
    path = index_path(settings, task_id)
    started = time.perf_counter()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        model = embedding_model or settings.embedding_model
        expected_digest = cache_digest(
            state_sha256, chunk_sha256, settings.embedding_provider, model
        )
        if not isinstance(payload, dict) or payload.get("complete") is not True:
            return None
        if payload.get("schema_version") != INDEX_SCHEMA:
            return None
        if payload.get("cache_digest") != expected_digest:
            return None
        if payload.get("state_sha256") != state_sha256 or payload.get("chunks_sha256") != chunk_sha256:
            return None
        if payload.get("embedding_provider") != settings.embedding_provider:
            return None
        if payload.get("embedding_model") != model:
            return None
        if payload.get("chunk_ids") != chunk_ids:
            return None
        matrix = np.asarray(payload.get("vectors"), dtype=np.float32)
        if matrix.ndim != 2 or matrix.shape[0] != len(chunk_ids) or matrix.shape[1] < 1:
            return None
        if not np.isfinite(matrix).all():
            return None
        norms = np.linalg.norm(matrix, axis=1)
        if np.any((norms != 0) & (np.abs(norms - 1.0) > 1e-3)):
            return None
        return PersistedVectors(
            vectors=matrix,
            load_ms=(time.perf_counter() - started) * 1000,
            cache_digest=expected_digest,
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def save_vectors(
    settings: AppSettings,
    task_id: str,
    *,
    state_sha256: str,
    chunk_sha256: str,
    chunk_ids: list[str],
    vectors: np.ndarray,
    embedding_model: str | None = None,
) -> dict[str, Any]:
    matrix = np.asarray(vectors, dtype=np.float32)
    if matrix.ndim != 2 or matrix.shape[0] != len(chunk_ids) or matrix.shape[1] < 1:
        raise ValueError("vector matrix does not match chunks")
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if not np.isfinite(matrix).all():
        raise ValueError("vectors must be finite")
    norms[norms == 0] = 1
    matrix = matrix / norms
    model = embedding_model or settings.embedding_model
    digest = cache_digest(
        state_sha256, chunk_sha256, settings.embedding_provider, model
    )
    payload = {
        "schema_version": INDEX_SCHEMA,
        "complete": True,
        "state_sha256": state_sha256,
        "chunks_sha256": chunk_sha256,
        "cache_digest": digest,
        "embedding_provider": settings.embedding_provider,
        "embedding_model": model,
        "chunk_ids": chunk_ids,
        "dimension": int(matrix.shape[1]),
        "vectors": matrix.tolist(),
    }
    path = index_path(settings, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp = Path(raw_temp)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return {
        "schema_version": INDEX_SCHEMA,
        "status": "ready",
        "embedding_provider": settings.embedding_provider,
        "embedding_model": model,
        "chunk_count": len(chunk_ids),
        "dimension": int(matrix.shape[1]),
        "cache_digest": digest[:16],
    }


def delete_index(settings: AppSettings, task_id: str) -> None:
    index_path(settings, task_id).unlink(missing_ok=True)
