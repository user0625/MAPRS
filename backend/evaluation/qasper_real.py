from __future__ import annotations

import argparse
import hashlib
import json
import math
import tempfile
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from itertools import product
from statistics import median
from typing import Any, Callable, Iterable, Literal

from backend.ask_retrieval import AskPaperRetrievalService
from backend.core.config import AppSettings, get_settings
from backend.evaluation.qasper import (
    QasperAdaptation,
    QasperCase,
    QasperPaper,
    load_adapted,
    load_qasper_state_index,
    sha256_file,
)
from backend.evaluation.upstream_preflight import run_preflight
from backend.reranker import BaseReranker
from backend.tools.embedder import BaseEmbedder


REAL_REPORT_SCHEMA = "public-paper-benchmark-v2"
CHECKPOINT_SCHEMA = "qasper-real-checkpoint-v2"
PILOT_QUOTAS = {"extractive": 45, "free_form": 20, "yes_no": 10, "unanswerable": 25}
PILOT_CANDIDATE_COUNTS = (20, 30, 40)
PILOT_EVIDENCE_COUNTS = (4, 6, 8)
PILOT_CROSS_VALIDATION_FOLDS = 5
EMBEDDING_REQUEST_LIMIT = 400
RERANK_REQUEST_LIMIT = 120


@dataclass
class RealRawCase:
    case_id: str
    paper_id: str
    candidate_scores: list[dict[str, Any]] = field(default_factory=list)
    bm25_scores: list[dict[str, Any]] = field(default_factory=list)
    vector_scores: list[dict[str, Any]] = field(default_factory=list)
    wall_latency_ms: float = 0.0
    query_latency_ms: float = 0.0
    reranker_latency_ms: float = 0.0
    index_build_ms: float = 0.0
    index_load_ms: float = 0.0
    index_persistent_cache_hit: bool = False
    index_memory_cache_hit: bool = False
    degraded_reason: str | None = None
    failure_category: str | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_sha(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(raw)
    try:
        with open(descriptor, "w", encoding="utf-8", closefd=True) as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _case_key(case: QasperCase) -> str:
    return f"{case.paper_id}\x1f{case.case_id}"


def _case_maps(dataset: QasperAdaptation) -> tuple[dict[str, QasperPaper], dict[str, QasperCase]]:
    papers = {paper.paper_id: paper for paper in dataset.papers}
    cases = {_case_key(case): case for paper in dataset.papers for case in paper.cases}
    if len(cases) != sum(len(paper.cases) for paper in dataset.papers):
        raise ValueError("QASPER contains duplicate paper/case identifiers")
    return papers, cases


def select_pilot_cases(
    dataset: QasperAdaptation,
    *,
    quotas: dict[str, int] | None = None,
    max_per_paper: int = 4,
) -> list[QasperCase]:
    """Select a deterministic, paper-capped train pilot with exact answer-type quotas."""
    if dataset.split != "train":
        raise ValueError("real pilot selection requires the QASPER train split")
    requested = dict(quotas or PILOT_QUOTAS)
    if set(requested) - set(PILOT_QUOTAS) or any(value < 0 for value in requested.values()):
        raise ValueError("pilot quotas contain an unsupported answer type or negative count")
    if max_per_paper < 1:
        raise ValueError("max_per_paper must be positive")
    answer_types = ("extractive", "free_form", "yes_no", "unanswerable")
    remaining = {answer_type: requested.get(answer_type, 0) for answer_type in answer_types}
    selected: list[QasperCase] = []
    used_papers: set[str] = set()
    while sum(remaining.values()) > 0:
        paper_choices: list[tuple[tuple[Any, ...], QasperPaper, tuple[int, ...], dict[str, list[QasperCase]]]] = []
        for paper in dataset.papers:
            if paper.paper_id in used_papers:
                continue
            groups = {
                answer_type: sorted(
                    (case for case in paper.cases if case.answer_type == answer_type),
                    key=lambda case: case.case_id,
                )
                for answer_type in answer_types
            }
            limits = [
                min(len(groups[answer_type]), remaining[answer_type], max_per_paper)
                for answer_type in answer_types
            ]
            combinations = [
                combination
                for combination in product(*(range(limit + 1) for limit in limits))
                if 0 < sum(combination) <= max_per_paper
            ]
            if not combinations:
                continue
            combination = max(combinations, key=lambda values: (
                sum(values),
                sum(
                    values[index] / max(1, remaining[answer_type])
                    for index, answer_type in enumerate(answer_types)
                ),
                values,
            ))
            count = sum(combination)
            # OpenAICompatibleEmbedder uses batches of eight. Prefer papers
            # that fill all four question slots and have the cheapest index.
            index_batches = math.ceil(len(paper.chunks) / 8)
            stable = hashlib.sha256(
                f"qasper-real-pilot-v2:{paper.paper_id}".encode("utf-8")
            ).hexdigest()
            paper_choices.append((
                (-count, index_batches / count, index_batches, stable),
                paper, combination, groups,
            ))
        if not paper_choices:
            missing = {key: value for key, value in remaining.items() if value}
            raise ValueError(f"pilot quotas unavailable under paper cap: {missing}")
        _, paper, combination, groups = min(paper_choices, key=lambda item: item[0])
        used_papers.add(paper.paper_id)
        for index, answer_type in enumerate(answer_types):
            selected.extend(groups[answer_type][: combination[index]])
            remaining[answer_type] -= combination[index]
    # Group by paper to ensure a paper index is built/loaded only once per contiguous run.
    selected.sort(key=lambda case: (case.paper_id, case.case_id))
    return selected


def _paper_disjoint_folds(
    cases: Iterable[QasperCase],
    *,
    fold_count: int = PILOT_CROSS_VALIDATION_FOLDS,
) -> list[list[QasperCase]]:
    """Partition train cases into deterministic, answer-type-balanced paper folds."""
    selected = list(cases)
    if fold_count < 1:
        raise ValueError("fold_count must be positive")
    by_paper: dict[str, list[QasperCase]] = {}
    for case in selected:
        by_paper.setdefault(case.paper_id, []).append(case)
    if not by_paper:
        raise ValueError("paper-disjoint folds require at least one case")
    actual_fold_count = min(fold_count, len(by_paper))
    totals = Counter(case.answer_type for case in selected)
    targets = {
        answer_type: count / actual_fold_count
        for answer_type, count in totals.items()
    }
    target_cases = len(selected) / actual_fold_count
    groups = sorted(
        (
            sorted(group, key=lambda case: case.case_id)
            for group in by_paper.values()
        ),
        key=lambda group: (
            -len(group),
            hashlib.sha256(
                f"qasper-real-cross-fold-v1:{group[0].paper_id}".encode("utf-8")
            ).hexdigest(),
        ),
    )
    folds: list[list[QasperCase]] = [[] for _ in range(actual_fold_count)]
    for index, group in enumerate(groups):
        if index < actual_fold_count:
            selected_fold = index
        else:
            added = Counter(case.answer_type for case in group)

            def imbalance(fold_index: int) -> tuple[float, int, int]:
                current = Counter(case.answer_type for case in folds[fold_index])
                type_load = sum(
                    ((current[answer_type] + added[answer_type]) / target) ** 2
                    for answer_type, target in targets.items() if target
                )
                case_load = ((len(folds[fold_index]) + len(group)) / target_cases) ** 2
                return type_load + case_load, len(folds[fold_index]), fold_index

            selected_fold = min(range(actual_fold_count), key=imbalance)
        folds[selected_fold].extend(group)
    for fold in folds:
        fold.sort(key=lambda case: (case.paper_id, case.case_id))
    return folds


def _cross_validation_metadata(folds: list[list[QasperCase]]) -> dict[str, Any]:
    assignment = [
        {"fold": fold_index, "paper_id": case.paper_id, "case_id": case.case_id}
        for fold_index, fold in enumerate(folds)
        for case in fold
    ]
    return {
        "fold_count": len(folds),
        "paper_disjoint": True,
        "assignment_sha256": _canonical_sha(assignment),
        "folds": [
            {
                "fold": fold_index,
                "paper_count": len({case.paper_id for case in fold}),
                "case_count": len(fold),
                "answer_type_counts": dict(sorted(Counter(
                    case.answer_type for case in fold
                ).items())),
            }
            for fold_index, fold in enumerate(folds)
        ],
    }


def _model_signature(settings: AppSettings) -> dict[str, Any]:
    # Deliberately excludes endpoints and credentials.
    return {
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.embedding_model,
        "reranker_provider": settings.ask_reranker_provider,
        "reranker_model": settings.ask_reranker_model,
        "reranker_timeout_seconds": settings.ask_reranker_timeout,
    }


def _dataset_signature(cache_dir: Path) -> dict[str, Any]:
    manifest_path = cache_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    splits = manifest.get("splits") or {}
    return {
        "manifest_sha256": sha256_file(manifest_path),
        "adapter_schema": manifest.get("schema_version"),
        "splits": {
            name: {
                key: item.get(key)
                for key in ("source_sha256", "adapted_sha256", "state_index_sha256", "case_count")
            }
            for name, item in sorted(splits.items()) if isinstance(item, dict)
        },
    }


def _split_dataset_sha(cache_dir: Path, split: str) -> str:
    signature = _dataset_signature(cache_dir)
    return _canonical_sha({
        "adapter_schema": signature.get("adapter_schema"),
        "split": split,
        "data": signature.get("splits", {}).get(split),
    })


def _counter(value: Any) -> int:
    raw = getattr(value, "request_count", 0)
    return int(raw) if isinstance(raw, int) else 0


def _safe_score_rows(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in value or []:
        if not isinstance(item, dict) or not item.get("chunk_id"):
            continue
        row: dict[str, Any] = {"chunk_id": str(item["chunk_id"])}
        for key in (
            "bm25_score", "bm25_rank", "vector_score", "vector_rank", "hybrid_score",
            "reranker_score", "score", "rank",
        ):
            number = item.get(key)
            if isinstance(number, (int, float)) and not isinstance(number, bool) and math.isfinite(number):
                row[key] = number
            elif number is None and key in item:
                row[key] = None
        rows.append(row)
    return rows


def _checkpoint_payload(
    split: str,
    dataset_sha256: str,
    model_sha256: str,
    case_selection_sha256: str,
    configuration_sha256: str,
    rows: list[RealRawCase],
    requests: dict[str, int],
) -> dict[str, Any]:
    return {
        "schema_version": CHECKPOINT_SCHEMA,
        "split": split,
        "dataset_sha256": dataset_sha256,
        "model_sha256": model_sha256,
        "case_selection_sha256": case_selection_sha256,
        "configuration_sha256": configuration_sha256,
        "completed": [asdict(row) for row in rows],
        "request_counts": requests,
        "failure_categories": dict(sorted(Counter(
            row.failure_category or row.degraded_reason for row in rows
            if row.failure_category or row.degraded_reason
        ).items())),
    }


def _load_checkpoint(
    path: Path,
    *,
    split: str,
    dataset_sha256: str,
    model_sha256: str,
    case_selection_sha256: str,
    configuration_sha256: str,
) -> tuple[list[RealRawCase], dict[str, int]]:
    if not path.exists():
        return [], {"embedding_batches": 0, "rerank_requests": 0}
    payload = json.loads(path.read_text(encoding="utf-8"))
    schema = payload.get("schema_version")
    if schema != CHECKPOINT_SCHEMA:
        suffix = "; legacy checkpoints are audit-only" if schema else ""
        raise ValueError(
            f"checkpoint schema mismatch: expected {CHECKPOINT_SCHEMA}, got {schema!r}{suffix}"
        )
    if payload.get("split") != split or payload.get("dataset_sha256") != dataset_sha256:
        raise ValueError("checkpoint dataset mismatch")
    if payload.get("model_sha256") != model_sha256:
        raise ValueError("checkpoint model mismatch")
    if payload.get("case_selection_sha256") != case_selection_sha256:
        raise ValueError("checkpoint sample selection mismatch")
    if payload.get("configuration_sha256") != configuration_sha256:
        raise ValueError("checkpoint configuration mismatch")
    rows = [RealRawCase(**item) for item in payload.get("completed") or []]
    raw_requests = payload.get("request_counts") or {}
    requests = {
        "embedding_batches": int(raw_requests.get("embedding_batches", 0)),
        "rerank_requests": int(raw_requests.get("rerank_requests", 0)),
    }
    return rows, requests


ServiceFactory = Callable[[AppSettings], AskPaperRetrievalService]


def collect_real_rows(
    cache_dir: Path,
    dataset: QasperAdaptation,
    cases: Iterable[QasperCase],
    settings: AppSettings,
    checkpoint_path: Path,
    *,
    embedder: BaseEmbedder | None = None,
    reranker: BaseReranker | None = None,
    service_factory: ServiceFactory | None = None,
    embedding_limit: int = EMBEDDING_REQUEST_LIMIT,
    rerank_limit: int = RERANK_REQUEST_LIMIT,
) -> tuple[list[RealRawCase], dict[str, int]]:
    if dataset.split == "test":
        raise ValueError("real retrieval collection must not access QASPER test")
    selected_cases = list(cases)
    state_index = load_qasper_state_index(cache_dir, dataset.split)
    data_sha = _split_dataset_sha(cache_dir, dataset.split)
    model_sha = _canonical_sha(_model_signature(settings))
    case_selection_sha = _canonical_sha([_case_key(case) for case in selected_cases])
    config_sha = _canonical_sha({
        "candidate_count": settings.ask_candidate_count,
        "evidence_count": settings.ask_evidence_count,
        "bm25_min_score": settings.ask_bm25_min_score,
        "vector_min_similarity": settings.ask_vector_min_similarity,
        "bm25_k1": settings.ask_bm25_k1,
        "bm25_b": settings.ask_bm25_b,
        "rrf_k": settings.ask_rrf_k,
        "mode": settings.ask_reranker_mode,
    })
    rows, requests = _load_checkpoint(
        checkpoint_path, split=dataset.split, dataset_sha256=data_sha,
        model_sha256=model_sha, case_selection_sha256=case_selection_sha,
        configuration_sha256=config_sha,
    )
    completed = {(row.paper_id, row.case_id) for row in rows}
    service = service_factory(settings) if service_factory else AskPaperRetrievalService(
        settings, embedder=embedder, reranker=reranker,
    )
    for case in selected_cases:
        if (case.paper_id, case.case_id) in completed:
            continue
        before_embedding = _counter(embedder or getattr(service, "embedder", None))
        before_rerank = _counter(reranker or getattr(service, "reranker", None))
        started = time.perf_counter()
        try:
            result = service.retrieve(
                case.paper_id,
                str(cache_dir / state_index[case.paper_id]["path"]),
                case.question,
            )
            wall_ms = (time.perf_counter() - started) * 1000
            diagnostics = result.diagnostics
            build_ms = float(getattr(diagnostics, "index_build_ms", 0.0) or 0.0)
            load_ms = float(getattr(diagnostics, "index_load_ms", 0.0) or 0.0)
            row = RealRawCase(
                case_id=case.case_id,
                paper_id=case.paper_id,
                candidate_scores=_safe_score_rows(getattr(diagnostics, "candidate_scores", [])),
                bm25_scores=_safe_score_rows(getattr(diagnostics, "bm25_scores_raw", [])),
                vector_scores=_safe_score_rows(getattr(diagnostics, "vector_scores_raw", [])),
                wall_latency_ms=wall_ms,
                query_latency_ms=max(0.0, wall_ms - build_ms - load_ms),
                reranker_latency_ms=float(getattr(diagnostics, "reranker_latency_ms", 0.0) or 0.0),
                index_build_ms=build_ms,
                index_load_ms=load_ms,
                index_persistent_cache_hit=bool(getattr(diagnostics, "index_cache_hit", False)),
                index_memory_cache_hit=bool(getattr(diagnostics, "index_memory_cache_hit", False)),
                degraded_reason=getattr(diagnostics, "degraded_reason", None),
            )
        except Exception as exc:
            wall_ms = (time.perf_counter() - started) * 1000
            row = RealRawCase(
                case_id=case.case_id,
                paper_id=case.paper_id,
                wall_latency_ms=wall_ms,
                query_latency_ms=wall_ms,
                degraded_reason=f"retrieval_failed:{type(exc).__name__}",
                failure_category=type(exc).__name__,
            )
        # Real clients expose request_count; injected fixtures may not. A
        # successful shadow retrieval still represents at most one rerank call.
        active_embedder = embedder or getattr(service, "embedder", None)
        active_reranker = reranker or getattr(service, "reranker", None)
        embedding_delta = max(0, _counter(active_embedder) - before_embedding)
        rerank_delta = max(0, _counter(active_reranker) - before_rerank)
        if active_reranker is None and settings.ask_reranker_mode != "disabled" and row.candidate_scores:
            rerank_delta = 1
        requests["embedding_batches"] += embedding_delta
        requests["rerank_requests"] += rerank_delta
        rows.append(row)
        _atomic_json(checkpoint_path, _checkpoint_payload(
            dataset.split, data_sha, model_sha, case_selection_sha, config_sha, rows, requests,
        ))
        if requests["embedding_batches"] > embedding_limit:
            raise RuntimeError(f"embedding request limit exceeded: {requests['embedding_batches']}>{embedding_limit}")
        if requests["rerank_requests"] > rerank_limit:
            raise RuntimeError(f"rerank request limit exceeded: {requests['rerank_requests']}>{rerank_limit}")
    return rows, requests


def _percentile(values: Iterable[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _tokenize(text: str) -> list[str]:
    import re
    return re.findall(r"[a-z0-9]+(?:[-'][a-z0-9]+)*|[\u3400-\u9fff]", text.casefold())


def _token_f1(prediction: str, expected: str) -> float:
    predicted, gold = Counter(_tokenize(prediction)), Counter(_tokenize(expected))
    overlap = sum((predicted & gold).values())
    if not predicted and not gold:
        return 1.0
    if not predicted or not gold or not overlap:
        return 0.0
    precision = overlap / sum(predicted.values())
    recall = overlap / sum(gold.values())
    return 2 * precision * recall / (precision + recall)


def _set_f1(predicted: set[str], gold: set[str]) -> float:
    if not predicted and not gold:
        return 1.0
    if not predicted or not gold:
        return 0.0
    precision = len(predicted & gold) / len(predicted)
    recall = len(predicted & gold) / len(gold)
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def _rrf_candidates(
    row: RealRawCase,
    *,
    candidate_count: int,
    bm25_min_score: float,
    vector_min_similarity: float,
    rrf_k: int,
    mode: Literal["hybrid", "bm25"] = "hybrid",
) -> list[str]:
    bm25 = [
        (str(item["chunk_id"]), float(item["score"]))
        for item in row.bm25_scores
        if isinstance(item.get("score"), (int, float)) and float(item["score"]) >= bm25_min_score
    ][:candidate_count]
    vector = [] if mode == "bm25" else [
        (str(item["chunk_id"]), float(item["score"]))
        for item in row.vector_scores
        if isinstance(item.get("score"), (int, float))
        and math.isfinite(float(item["score"]))
        and float(item["score"]) > 0
        and float(item["score"]) >= vector_min_similarity
    ][:candidate_count]
    order: dict[str, int] = {}
    scores: dict[str, float] = {}
    for ranking in (bm25, vector):
        for rank, (chunk_id, _) in enumerate(ranking, 1):
            order.setdefault(chunk_id, len(order))
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1 / (rrf_k + rank)
    return [
        chunk_id for chunk_id, _ in sorted(
            scores.items(), key=lambda item: (-item[1], order[item[0]])
        )[:candidate_count]
    ]


def replay_report(
    name: str,
    rows: list[RealRawCase],
    dataset: QasperAdaptation,
    *,
    candidate_count: int,
    evidence_count: int,
    bm25_min_score: float,
    vector_min_similarity: float,
    rrf_k: int,
    rerank: bool,
    evidence_threshold: float = 0.0,
    answerability_threshold: float = 0.0,
    mode: Literal["hybrid", "bm25"] = "hybrid",
) -> dict[str, Any]:
    papers, cases = _case_maps(dataset)
    observations: list[dict[str, Any]] = []
    for row in rows:
        case = cases.get(f"{row.paper_id}\x1f{row.case_id}")
        if case is None:
            raise ValueError(f"checkpoint contains an unknown case ID: {row.case_id}")
        candidates = _rrf_candidates(
            row, candidate_count=candidate_count, bm25_min_score=bm25_min_score,
            vector_min_similarity=vector_min_similarity, rrf_k=rrf_k, mode=mode,
        )
        score_map = {
            str(item["chunk_id"]): float(item["reranker_score"])
            for item in row.candidate_scores
            if isinstance(item.get("reranker_score"), (int, float))
        }
        used_reranker = rerank and bool(score_map)
        refusal_reason: str | None = None
        if used_reranker:
            ranked = sorted(
                candidates,
                key=lambda chunk_id: (-score_map.get(chunk_id, -math.inf), candidates.index(chunk_id)),
            )
            top_score = max((score_map.get(chunk_id, -math.inf) for chunk_id in candidates), default=-math.inf)
            refused = top_score < answerability_threshold
            if refused:
                refusal_reason = "answerability_threshold"
            selected = [] if refused else [
                chunk_id for chunk_id in ranked
                if score_map.get(chunk_id, -math.inf) >= evidence_threshold
            ][:evidence_count]
            if not refused and not selected:
                refused = True
                refusal_reason = "evidence_filter_empty"
        else:
            # This exactly mirrors production's explicit reranker/BM25 fallback.
            selected = candidates[:evidence_count]
            refused = not selected
        relevant = set(case.relevant_chunk_ids)
        selected_set = set(selected)
        top_six = selected[:6]
        top_six_set = set(top_six)
        first = next((rank for rank, chunk_id in enumerate(top_six, 1) if chunk_id in relevant), None)
        evidence_sets = [set(group) for group in case.minimum_evidence_sets]
        chunk_map = {chunk.chunk_id: chunk.text for chunk in papers[case.paper_id].chunks}
        prediction = chunk_map.get(selected[0], "") if selected else ""
        answer_f1 = (
            1.0 if case.answer_type == "unanswerable" and refused else
            max((_token_f1(prediction, gold) for gold in case.gold_answers), default=0.0)
        )
        latency = row.query_latency_ms if rerank else max(
            0.0, row.query_latency_ms - row.reranker_latency_ms
        )
        scenario_degraded_reason = row.degraded_reason
        if not rerank and (scenario_degraded_reason or "").startswith("reranker_unavailable:"):
            scenario_degraded_reason = None
        observations.append({
            "case_id": case.case_id,
            "answer_type": case.answer_type,
            "answerable": case.answerable,
            "candidate_recall": (
                len(set(candidates[:20]) & relevant) / len(relevant) if relevant else None
            ),
            "recall": len(top_six_set & relevant) / len(relevant) if relevant else None,
            "precision": (
                len(top_six_set & relevant) / len(top_six) if relevant and top_six else
                (0.0 if relevant else None)
            ),
            "mrr": 1 / first if first else (0.0 if relevant else None),
            "evidence_f1": max((_set_f1(selected_set, group) for group in evidence_sets), default=None),
            "coverage": any(group <= selected_set for group in evidence_sets) if evidence_sets else None,
            "refused": refused,
            "refusal_reason": refusal_reason,
            "token_f1": answer_f1,
            "latency_ms": latency,
            "degraded_reason": scenario_degraded_reason,
        })
    answerable = [item for item in observations if item["answerable"]]
    unanswerable = [item for item in observations if not item["answerable"]]

    def average(group: list[dict[str, Any]], key: str) -> float:
        values = [float(item[key]) for item in group if item.get(key) is not None]
        return sum(values) / len(values) if values else 0.0

    query_latencies = [float(item["latency_ms"]) for item in observations]
    metrics = {
        "candidate_recall_at_20": average(answerable, "candidate_recall"),
        "recall_at_6": average(answerable, "recall"),
        "precision_at_6": average(answerable, "precision"),
        "mrr": average(answerable, "mrr"),
        "evidence_coverage": average(answerable, "coverage"),
        "evidence_f1": average(answerable, "evidence_f1"),
        "unanswerable_refusal_rate": average(unanswerable, "refused"),
        "answerable_false_refusal_rate": average(answerable, "refused"),
        "answerability_threshold_refusal_rate": sum(
            item["refusal_reason"] == "answerability_threshold" for item in observations
        ) / max(1, len(observations)),
        "evidence_filter_empty_rate": sum(
            item["refusal_reason"] == "evidence_filter_empty" for item in observations
        ) / max(1, len(observations)),
        "unanswerable_answerability_refusal_rate": sum(
            item["refusal_reason"] == "answerability_threshold" for item in unanswerable
        ) / max(1, len(unanswerable)),
        "unanswerable_evidence_empty_refusal_rate": sum(
            item["refusal_reason"] == "evidence_filter_empty" for item in unanswerable
        ) / max(1, len(unanswerable)),
        "answerable_answerability_false_refusal_rate": sum(
            item["refusal_reason"] == "answerability_threshold" for item in answerable
        ) / max(1, len(answerable)),
        "answerable_evidence_empty_false_refusal_rate": sum(
            item["refusal_reason"] == "evidence_filter_empty" for item in answerable
        ) / max(1, len(answerable)),
        "answer_token_f1": average(observations, "token_f1"),
        "citation_validity_rate": 1.0,
        "evidence_support_rate": 1.0,
        "latency_p50_ms": median(query_latencies) if query_latencies else 0.0,
        "latency_p95_ms": _percentile(query_latencies, 0.95),
        "estimated_cost_usd": 0.0,
        "degradation_rate": sum(bool(item["degraded_reason"]) for item in observations) / max(1, len(observations)),
    }
    failures = [
        item["case_id"] for item in observations
        if (item["answerable"] and (item["recall"] or 0) < 1)
        or (not item["answerable"] and not item["refused"])
    ]
    return {
        "scenario": name,
        "effective_modes": sorted({
            "bm25" if mode == "bm25" else
            ("reranker" if rerank and not item["degraded_reason"] else "hybrid")
            for item in observations
        }),
        "case_count": len(observations),
        "metrics": metrics,
        "answer_quality_by_type": {
            kind: {"count": len(group), "token_f1": average(group, "token_f1")}
            for kind in ("extractive", "yes_no", "free_form", "unanswerable")
            if (group := [item for item in observations if item["answer_type"] == kind])
        },
        "degraded_reasons": sorted({
            str(item["degraded_reason"]) for item in observations if item["degraded_reason"]
        }),
        "failure_case_ids": failures,
    }


def _observed_boundaries(fixed: Iterable[float], values: Iterable[float]) -> list[float]:
    observed = sorted({float(value) for value in values if math.isfinite(float(value))})
    sampled: list[float] = []
    if observed:
        sampled = [_percentile(observed, fraction / 10) for fraction in range(11)]
    combined = sorted(set(float(item) for item in fixed) | set(sampled))
    midpoints = [(left + right) / 2 for left, right in zip(combined, combined[1:])]
    return sorted(set(combined + midpoints))


def _gate(name: str, rules: list[tuple[bool, str]]) -> dict[str, Any]:
    failures = [message for passed, message in rules if not passed]
    return {"name": name, "passed": not failures, "failures": failures}


def _retrieval_gate(
    hybrid: dict[str, Any],
    bm25: dict[str, Any],
) -> dict[str, Any]:
    h, b = hybrid["metrics"], bm25["metrics"]
    return _gate("retrieval", [
        (h["candidate_recall_at_20"] >= 0.85, "hybrid candidate_recall_at_20 >= 0.85"),
        (h["candidate_recall_at_20"] >= b["candidate_recall_at_20"], "hybrid candidate recall >= BM25"),
        (h["degradation_rate"] <= 0.01, "degradation rate <= 0.01"),
        (h["latency_p95_ms"] <= 3000, "query p95 <= 3000 ms"),
    ])


def _pilot_gates(
    hybrid: dict[str, Any],
    bm25: dict[str, Any],
    reranked: dict[str, Any],
    best_non_rerank: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    r, n = reranked["metrics"], best_non_rerank["metrics"]
    retrieval = _retrieval_gate(hybrid, bm25)
    reranker = _gate("reranker", [
        (r["precision_at_6"] - n["precision_at_6"] >= 0.05, "reranker precision uplift >= 0.05"),
        (r["recall_at_6"] - n["recall_at_6"] >= -0.02, "reranker recall delta >= -0.02"),
    ])
    refusal = _gate("refusal", [
        (r["unanswerable_refusal_rate"] >= 0.70, "unanswerable refusal >= 0.70"),
        (r["answerable_false_refusal_rate"] <= 0.10, "answerable false refusal <= 0.10"),
    ])
    return {"retrieval": retrieval, "reranker": reranker, "refusal": refusal}


def _validation_failures(report: dict[str, Any]) -> list[str]:
    metrics = report["metrics"]
    rules = {
        "candidate_recall_at_20": (metrics["candidate_recall_at_20"] >= 0.90, ">= 0.90"),
        "recall_at_6": (metrics["recall_at_6"] >= 0.70, ">= 0.70"),
        "precision_at_6": (metrics["precision_at_6"] >= 0.20, ">= 0.20"),
        "mrr": (metrics["mrr"] >= 0.55, ">= 0.55"),
        "evidence_coverage": (metrics["evidence_coverage"] >= 0.70, ">= 0.70"),
        "evidence_f1": (metrics["evidence_f1"] >= 0.30, ">= 0.30"),
        "degradation_rate": (metrics["degradation_rate"] <= 0.01, "<= 0.01"),
        "latency_p95_ms": (metrics["latency_p95_ms"] <= 3000, "<= 3000"),
    }
    return [
        f"{name}={metrics[name]:.4f} (expected {expected})"
        for name, (passed, expected) in rules.items() if not passed
    ]


def _validation_proxy_gate(report: dict[str, Any]) -> dict[str, Any]:
    failures = _validation_failures(report)
    return {
        "name": "pilot_validation_proxy",
        "passed": not failures,
        "failures": failures,
    }


def _cross_fold_proxy_gate(reports: Iterable[dict[str, Any]]) -> dict[str, Any]:
    failures = [
        f"fold_{fold_index}: {failure}"
        for fold_index, report in enumerate(reports)
        for failure in _validation_failures(report)
    ]
    return {
        "name": "train_cross_validation_proxy",
        "passed": not failures,
        "failures": failures,
    }


def _worst_fold_metrics(reports: Iterable[dict[str, Any]]) -> dict[str, float]:
    metrics = [report["metrics"] for report in reports]
    if not metrics:
        raise ValueError("cross-fold selection requires at least one fold report")
    return {
        "candidate_recall_at_20": min(item["candidate_recall_at_20"] for item in metrics),
        "recall_at_6": min(item["recall_at_6"] for item in metrics),
        "precision_at_6": min(item["precision_at_6"] for item in metrics),
        "mrr": min(item["mrr"] for item in metrics),
        "evidence_coverage": min(item["evidence_coverage"] for item in metrics),
        "evidence_f1": min(item["evidence_f1"] for item in metrics),
        "degradation_rate": max(item["degradation_rate"] for item in metrics),
        "latency_p95_ms": max(item["latency_p95_ms"] for item in metrics),
    }


@dataclass(frozen=True)
class HybridCandidate:
    priority: tuple[float, ...]
    report: dict[str, Any]
    configuration: dict[str, Any]
    fold_reports: tuple[dict[str, Any], ...]


def _cross_fold_priority(candidate: HybridCandidate) -> tuple[float, ...]:
    gate = _cross_fold_proxy_gate(candidate.fold_reports)
    worst = _worst_fold_metrics(candidate.fold_reports)
    return (
        -len(gate["failures"]),
        worst["candidate_recall_at_20"],
        worst["recall_at_6"],
        worst["precision_at_6"],
        worst["mrr"],
        worst["evidence_coverage"],
        worst["evidence_f1"],
        -worst["degradation_rate"],
        -worst["latency_p95_ms"],
        *candidate.priority,
    )


def _select_hybrid_candidate(
    candidates: list[HybridCandidate],
    bm25: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Select for paper-disjoint train robustness without weakening Retrieval."""
    if not candidates:
        raise ValueError("pilot produced no Hybrid configurations")
    retrieval_qualified = [
        item for item in candidates if _retrieval_gate(item.report, bm25)["passed"]
    ]
    validation_proxy_qualified = [
        item for item in retrieval_qualified
        if _validation_proxy_gate(item.report)["passed"]
    ]
    cross_fold_proxy_qualified = [
        item for item in retrieval_qualified
        if _cross_fold_proxy_gate(item.fold_reports)["passed"]
    ]
    base_pool = retrieval_qualified or candidates
    selection_pool = cross_fold_proxy_qualified or base_pool
    selected = max(
        selection_pool, key=_cross_fold_priority
    )
    selected_cross_fold_gate = _cross_fold_proxy_gate(selected.fold_reports)
    selection = {
        "strategy": "retrieval_gate_then_paper_disjoint_worst_fold",
        "retrieval_qualified_configuration_count": len(retrieval_qualified),
        "validation_proxy_qualified_configuration_count": len(
            validation_proxy_qualified
        ),
        "cross_fold_validation_proxy_qualified_configuration_count": len(
            cross_fold_proxy_qualified
        ),
        "cross_fold_fallback_used": not bool(cross_fold_proxy_qualified),
        "selected_configuration_proxy_gate": _validation_proxy_gate(selected.report),
        "selected_configuration_cross_fold_proxy_gate": selected_cross_fold_gate,
        "selected_configuration_worst_fold_metrics": _worst_fold_metrics(
            selected.fold_reports
        ),
        "selected_configuration_fold_metrics": [
            {"fold": fold_index, "metrics": report["metrics"]}
            for fold_index, report in enumerate(selected.fold_reports)
        ],
    }
    return selected.report, selected.configuration, selection


def _public_configuration(settings: AppSettings, selected: dict[str, Any]) -> dict[str, Any]:
    return {
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.embedding_model,
        "reranker_provider": settings.ask_reranker_provider,
        "reranker_model": settings.ask_reranker_model,
        "bm25_k1": settings.ask_bm25_k1,
        "bm25_b": settings.ask_bm25_b,
        "rrf_k": settings.ask_rrf_k,
        **selected,
    }


def _configuration_fingerprint(
    settings: AppSettings, selected: dict[str, Any],
) -> dict[str, Any]:
    return {
        "models": _model_signature(settings),
        "bm25_k1": settings.ask_bm25_k1,
        "bm25_b": settings.ask_bm25_b,
        "selected": selected,
    }


def _base_report(
    *,
    run_level: Literal["pilot", "validation"],
    dataset: QasperAdaptation,
    settings: AppSettings,
    data_signature: dict[str, Any],
    hybrid_configuration: dict[str, Any],
    reranker_configuration: dict[str, Any] | None,
    scenarios: list[dict[str, Any]],
    requests: dict[str, int | float],
    failures: list[str],
    version: str,
    quality_gates: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    passed = not failures and all(
        gate.get("passed") for gate in (quality_gates or {}).values()
    )
    evaluated_papers = int(requests.get("evaluated_paper_count", len(dataset.papers)))
    return {
        "schema_version": REAL_REPORT_SCHEMA,
        "benchmark": "QASPER",
        "result_status": "public_real_model_run",
        "run_level": run_level,
        "run_version": version,
        "dataset_adapter_version": dataset.schema_version,
        "split": dataset.split,
        "generated_at": _utc_now(),
        "scope": {
            "evaluates": ["full-text retrieval", "evidence selection", "refusal"],
            "does_not_evaluate": ["PDF parsing", "page-number accuracy", "generative answer quality"],
            "answer_baseline": "top-retrieved-paragraph diagnostic only; generation is unchanged",
        },
        "paper_count": len(dataset.papers),
        "dataset_paper_count": len(dataset.papers),
        "evaluated_paper_count": evaluated_papers,
        "case_count": sum(item["case_count"] for item in scenarios[:1]),
        "exclusions": dataset.exclusions,
        "data_signature": data_signature,
        "configuration": _public_configuration(settings, hybrid_configuration),
        "hybrid_configuration": hybrid_configuration,
        "reranker_configuration": reranker_configuration,
        "configuration_sha256": _canonical_sha(
            _configuration_fingerprint(settings, hybrid_configuration)
        ),
        "request_counts": {
            "embedding_batches": requests.get("embedding_batches", 0),
            "rerank_requests": requests.get("rerank_requests", 0),
        },
        "latency": {
            "index_build_ms": round(float(requests.get("index_build_ms", 0)), 3),
            "index_load_ms": round(float(requests.get("index_load_ms", 0)), 3),
            "query_excludes_current_index_build_load": True,
            "wall_latency_persisted_in_checkpoint": True,
            "persistent_cache_hits": int(requests.get("persistent_cache_hits", 0)),
            "memory_cache_hits": int(requests.get("memory_cache_hits", 0)),
        },
        "cost": {"estimated_usd": 0.0, "pricing_configured": False},
        "quality_gate": {"passed": passed, "failures": failures},
        "quality_gates": quality_gates or {},
        "degradation": {
            "rate": next(
                (item["metrics"].get("degradation_rate", 0.0) for item in scenarios
                 if item["scenario"] == "real-hybrid"),
                scenarios[-1]["metrics"].get("degradation_rate", 0.0),
            ),
            "reasons": next(
                (item.get("degraded_reasons", []) for item in scenarios
                 if item["scenario"] == "real-hybrid"),
                scenarios[-1].get("degraded_reasons", []),
            ),
        },
        "production_recommendation": {
            "embedding": (
                "candidate_default" if run_level == "validation" and passed
                else "candidate_for_validation" if run_level == "pilot"
                and bool((quality_gates or {}).get("retrieval", {}).get("passed"))
                else "keep_current"
            ),
            "reranker": "disabled",
        },
        "test_accessed": False,
        "scenarios": scenarios,
    }


def run_real_pilot(
    cache_dir: Path,
    output: Path,
    pilot_version: str,
    *,
    settings: AppSettings | None = None,
    checkpoint_path: Path | None = None,
    embedder: BaseEmbedder | None = None,
    reranker: BaseReranker | None = None,
    service_factory: ServiceFactory | None = None,
    preflight_runner: Callable[..., dict[str, Any]] = run_preflight,
    require_preflight: bool = True,
    quotas: dict[str, int] | None = None,
) -> dict[str, Any]:
    if output.exists():
        raise ValueError("real pilot output already exists")
    dataset = load_adapted(cache_dir, "train")
    cases = select_pilot_cases(dataset, quotas=quotas)
    cross_validation_folds = _paper_disjoint_folds(cases)
    configured = (settings or get_settings()).model_copy(update={
        "ask_candidate_count": max(PILOT_CANDIDATE_COUNTS),
        "ask_evidence_count": max(PILOT_EVIDENCE_COUNTS),
        "ask_bm25_min_score": 0.0,
        "ask_vector_min_similarity": -1.0,
        "ask_reranker_mode": "shadow",
        "ask_reranker_timeout": 10.0,
    })
    preflight = None
    if require_preflight:
        preflight = preflight_runner(configured, embedder=embedder, reranker=reranker)
        if not preflight.get("ok"):
            categories = [
                {"service": item.get("service"), "category": item.get("category")}
                for item in preflight.get("checks", []) if not item.get("ok")
            ]
            raise ValueError(f"upstream preflight failed: {json.dumps(categories, separators=(',', ':'))}")
    checkpoint = checkpoint_path or output.with_suffix(".checkpoint.json")
    rows, requests = collect_real_rows(
        cache_dir, dataset, cases, configured, checkpoint,
        embedder=embedder, reranker=reranker, service_factory=service_factory,
    )
    rows_by_case = {
        f"{row.paper_id}\x1f{row.case_id}": row
        for row in rows
    }
    fold_rows = [
        [rows_by_case[_case_key(case)] for case in fold]
        for fold in cross_validation_folds
    ]
    request_summary: dict[str, int | float] = {
        **requests,
        "index_build_ms": sum(row.index_build_ms for row in rows),
        "index_load_ms": sum(row.index_load_ms for row in rows),
        "evaluated_paper_count": len({row.paper_id for row in rows}),
        "persistent_cache_hits": sum(row.index_persistent_cache_hit for row in rows),
        "memory_cache_hits": sum(row.index_memory_cache_hit for row in rows),
    }
    bm25_values = [
        float(item["score"]) for row in rows for item in row.bm25_scores
        if isinstance(item.get("score"), (int, float))
    ]
    vector_values = [
        float(item["score"]) for row in rows for item in row.vector_scores
        if isinstance(item.get("score"), (int, float)) and float(item["score"]) >= 0
    ]
    bm25_thresholds = _observed_boundaries((0.0, 0.5, 1.0, 2.0, 4.0, 8.0), bm25_values)
    vector_thresholds = _observed_boundaries((0.0, 0.1, 0.2, 0.3, 0.4, 0.5), vector_values)
    bm25_candidates: list[tuple[tuple[float, ...], dict[str, Any], dict[str, Any]]] = []
    hybrid_candidates: list[HybridCandidate] = []
    for candidate_count in PILOT_CANDIDATE_COUNTS:
        for evidence_count in PILOT_EVIDENCE_COUNTS:
            for bm25_threshold in bm25_thresholds:
                common: dict[str, Any] = {
                    "candidate_count": candidate_count, "evidence_count": evidence_count,
                    "bm25_min_score": bm25_threshold, "rrf_k": configured.ask_rrf_k,
                }
                baseline = replay_report(
                    "bm25", rows, dataset, **common, vector_min_similarity=1.0,
                    rerank=False, mode="bm25",
                )
                metric = baseline["metrics"]
                bm25_candidates.append(((
                    metric["candidate_recall_at_20"], metric["recall_at_6"],
                    metric["precision_at_6"], metric["mrr"],
                ), baseline, {**common, "vector_min_similarity": None}))
                for vector_threshold in vector_thresholds:
                    hybrid = replay_report(
                        "real-hybrid", rows, dataset, **common,
                        vector_min_similarity=vector_threshold, rerank=False,
                    )
                    metric = hybrid["metrics"]
                    fold_reports = tuple(
                        {"metrics": replay_report(
                            f"real-hybrid-fold-{fold_index}", fold, dataset,
                            **common, vector_min_similarity=vector_threshold,
                            rerank=False,
                        )["metrics"]}
                        for fold_index, fold in enumerate(fold_rows)
                    )
                    hybrid_candidates.append(HybridCandidate(
                        priority=(
                            metric["candidate_recall_at_20"], metric["recall_at_6"],
                            metric["precision_at_6"], metric["mrr"],
                        ),
                        report=hybrid,
                        configuration={
                            **common, "vector_min_similarity": vector_threshold,
                        },
                        fold_reports=fold_reports,
                    ))
    _, best_bm25, bm25_config = max(bm25_candidates, key=lambda item: item[0])
    best_hybrid, retrieval_config, hybrid_selection = _select_hybrid_candidate(
        hybrid_candidates, best_bm25
    )
    reranker_values = [
        float(item["reranker_score"]) for row in rows for item in row.candidate_scores
        if isinstance(item.get("reranker_score"), (int, float))
    ]
    top_values = [
        max(scores) for row in rows
        if (scores := [
            float(item["reranker_score"]) for item in row.candidate_scores
            if isinstance(item.get("reranker_score"), (int, float))
        ])
    ]
    evidence_thresholds = _observed_boundaries((0.0, 0.1, 0.3, 0.5, 0.7, 0.9), reranker_values)
    answerability_thresholds = _observed_boundaries((0.0, 0.1, 0.3, 0.5, 0.7, 0.9), top_values)
    reranked_candidates: list[
        tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]
    ] = []
    for evidence_count in PILOT_EVIDENCE_COUNTS:
        for evidence_threshold in evidence_thresholds:
            for answerability_threshold in answerability_thresholds:
                selected = {
                    **retrieval_config,
                    "evidence_count": evidence_count,
                    "evidence_threshold": evidence_threshold,
                    "answerability_threshold": answerability_threshold,
                }
                report = replay_report(
                    "real-reranker", rows, dataset, rerank=True, **selected,
                )
                gates = _pilot_gates(best_hybrid, best_bm25, report, best_hybrid)
                reranked_candidates.append((report, selected, gates))
    if not reranked_candidates:
        raise ValueError("pilot produced no complete reranker scores")
    recall_report, recall_config, recall_gates = max(
        reranked_candidates,
        key=lambda item: (
            item[0]["metrics"]["recall_at_6"],
            item[0]["metrics"]["precision_at_6"],
            item[0]["metrics"]["unanswerable_refusal_rate"],
            -item[0]["metrics"]["answerable_false_refusal_rate"],
        ),
    )
    precision_report, precision_config, precision_gates = max(
        reranked_candidates,
        key=lambda item: (
            item[0]["metrics"]["precision_at_6"],
            item[0]["metrics"]["recall_at_6"],
            item[0]["metrics"]["unanswerable_refusal_rate"],
            -item[0]["metrics"]["answerable_false_refusal_rate"],
        ),
    )
    reranker_choice = max(
        reranked_candidates,
        key=lambda item: (
            -len(item[2]["reranker"]["failures"]),
            item[0]["metrics"]["precision_at_6"],
            item[0]["metrics"]["recall_at_6"],
        ),
    )
    refusal_choice = max(
        reranked_candidates,
        key=lambda item: (
            -len(item[2]["refusal"]["failures"]),
            item[0]["metrics"]["unanswerable_refusal_rate"],
            -item[0]["metrics"]["answerable_false_refusal_rate"],
        ),
    )
    quality_gates = {
        "retrieval": recall_gates["retrieval"],
        "reranker": reranker_choice[2]["reranker"],
        "refusal": refusal_choice[2]["refusal"],
    }
    feasible = [
        item for item in reranked_candidates
        if item[2]["reranker"]["passed"] and item[2]["refusal"]["passed"]
    ]
    selected_reranker = (
        max(feasible, key=lambda item: (
            item[0]["metrics"]["precision_at_6"], item[0]["metrics"]["recall_at_6"]
        ))[1]
        if feasible else None
    )
    failures = [
        failure
        for gate in quality_gates.values()
        for failure in gate["failures"]
    ]
    recall_report = {**recall_report, "scenario": "reranker-recall-protection"}
    precision_report = {**precision_report, "scenario": "reranker-precision-first"}
    report = _base_report(
        run_level="pilot", dataset=dataset, settings=configured,
        data_signature=_dataset_signature(cache_dir),
        hybrid_configuration=retrieval_config,
        reranker_configuration=selected_reranker,
        scenarios=[best_bm25, best_hybrid, recall_report, precision_report],
        requests=request_summary, failures=failures, version=pilot_version,
        quality_gates=quality_gates,
    )
    report.update({
        "pilot_quota": dict(quotas or PILOT_QUOTAS),
        "max_questions_per_paper": 4,
        "upstream_preflight": preflight,
        "request_limits": {
            "embedding_batches": EMBEDDING_REQUEST_LIMIT,
            "rerank_requests": RERANK_REQUEST_LIMIT,
        },
        "bm25_configuration": bm25_config,
        "hybrid_selection": hybrid_selection,
        "train_cross_validation": _cross_validation_metadata(
            cross_validation_folds
        ),
        "reranker_diagnostics": {
            "evaluated_configuration_count": len(reranked_candidates),
            "feasible_configuration_count": len(feasible),
            "recall_protection": {
                "configuration": recall_config,
                "gates": {
                    "reranker": recall_gates["reranker"],
                    "refusal": recall_gates["refusal"],
                },
            },
            "precision_first": {
                "configuration": precision_config,
                "gates": {
                    "reranker": precision_gates["reranker"],
                    "refusal": precision_gates["refusal"],
                },
            },
        },
        "selection_grid": {
            "candidate_counts": list(PILOT_CANDIDATE_COUNTS),
            "evidence_counts": list(PILOT_EVIDENCE_COUNTS),
            "bm25_threshold_count": len(bm25_thresholds),
            "vector_threshold_count": len(vector_thresholds),
            "evidence_threshold_count": len(evidence_thresholds),
            "answerability_threshold_count": len(answerability_thresholds),
            "upstream_collections": 1,
            "offline_replay_only": True,
        },
        "validation_authorized": quality_gates["retrieval"]["passed"],
        "validation_recommended": (
            quality_gates["retrieval"]["passed"]
            and hybrid_selection[
                "selected_configuration_cross_fold_proxy_gate"
            ]["passed"]
        ),
        "validation_scope": (
            "retrieval_only" if quality_gates["retrieval"]["passed"] else None
        ),
        "failure_dimensions": {
            "gate": failures,
            "answer_type": dict(sorted(Counter(
                case.answer_type for case in cases
                if case.case_id in set(
                    recall_report["failure_case_ids"] + precision_report["failure_case_ids"]
                )
            ).items())),
            "degradation": dict(sorted(Counter(
                row.degraded_reason for row in rows if row.degraded_reason
            ).items())),
        },
    })
    if not report["validation_recommended"]:
        report["production_recommendation"]["embedding"] = "keep_current"
    _atomic_json(output, report)
    return report


def run_real_validation(
    cache_dir: Path,
    pilot_path: Path,
    output: Path,
    calibration_version: str,
    *,
    settings: AppSettings | None = None,
    checkpoint_path: Path | None = None,
    embedder: BaseEmbedder | None = None,
    reranker: BaseReranker | None = None,
    service_factory: ServiceFactory | None = None,
    preflight_runner: Callable[..., dict[str, Any]] = run_preflight,
    require_preflight: bool = True,
) -> dict[str, Any]:
    if output.exists():
        raise ValueError("real validation output already exists")
    registry_path = cache_dir / "real-calibration-versions.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8")) if registry_path.exists() else {}
    if calibration_version in registry:
        raise ValueError("calibration version already exists")
    pilot = json.loads(pilot_path.read_text(encoding="utf-8"))
    if (
        pilot.get("schema_version") != REAL_REPORT_SCHEMA
        or pilot.get("run_level") != "pilot"
        or pilot.get("split") != "train"
        or not pilot.get("validation_authorized")
        or pilot.get("validation_scope") != "retrieval_only"
        or not pilot.get("quality_gates", {}).get("retrieval", {}).get("passed")
        or pilot.get("validation_recommended") is False
    ):
        raise ValueError(
            "real validation requires a retrieval-authorized and "
            "validation-recommended train Pilot artifact"
        )
    data_signature = _dataset_signature(cache_dir)
    if pilot.get("data_signature") != data_signature:
        raise ValueError("Pilot dataset SHA does not match the imported QASPER cache")
    pilot_hybrid = pilot.get("hybrid_configuration") or pilot.get("configuration") or {}
    selected = {
        key: pilot_hybrid[key]
        for key in (
            "candidate_count", "evidence_count", "bm25_min_score",
            "vector_min_similarity", "rrf_k",
        )
    }
    configured = (settings or get_settings()).model_copy(update={
        "ask_candidate_count": int(selected["candidate_count"]),
        "ask_evidence_count": int(selected["evidence_count"]),
        "ask_bm25_min_score": float(selected["bm25_min_score"]),
        "ask_vector_min_similarity": float(selected["vector_min_similarity"]),
        "ask_rrf_k": int(selected["rrf_k"]),
        "ask_reranker_mode": "disabled",
        # Retain the Pilot model fingerprint while keeping invocation disabled.
        "ask_reranker_timeout": 10.0,
    })
    expected_config_sha = _canonical_sha(_configuration_fingerprint(configured, selected))
    if pilot.get("configuration_sha256") != expected_config_sha:
        raise ValueError("Pilot model/configuration SHA does not match validation settings")
    preflight = None
    if require_preflight:
        preflight = preflight_runner(
            configured, embedder=embedder, reranker=reranker, include_reranker=False,
        )
        if not preflight.get("ok"):
            raise ValueError("upstream preflight failed; validation was not started")
    dataset = load_adapted(cache_dir, "validation")
    cases = [case for paper in dataset.papers for case in paper.cases]
    checkpoint = checkpoint_path or output.with_suffix(".checkpoint.json")
    rows, requests = collect_real_rows(
        cache_dir, dataset, cases, configured, checkpoint,
        embedder=embedder, reranker=reranker, service_factory=service_factory,
        embedding_limit=max(EMBEDDING_REQUEST_LIMIT, 10000),
        rerank_limit=0,
    )
    request_summary: dict[str, int | float] = {
        **requests,
        "index_build_ms": sum(row.index_build_ms for row in rows),
        "index_load_ms": sum(row.index_load_ms for row in rows),
        "evaluated_paper_count": len({row.paper_id for row in rows}),
        "persistent_cache_hits": sum(row.index_persistent_cache_hit for row in rows),
        "memory_cache_hits": sum(row.index_memory_cache_hit for row in rows),
    }
    baseline_config = pilot.get("bm25_configuration") or selected
    bm25 = replay_report(
        "bm25-offline-baseline", rows, dataset,
        candidate_count=int(baseline_config["candidate_count"]),
        evidence_count=int(baseline_config["evidence_count"]),
        bm25_min_score=float(baseline_config["bm25_min_score"]),
        vector_min_similarity=1.0, rrf_k=int(baseline_config["rrf_k"]),
        rerank=False, mode="bm25",
    )
    hybrid = replay_report(
        "real-hybrid", rows, dataset, rerank=False,
        candidate_count=int(selected["candidate_count"]),
        evidence_count=int(selected["evidence_count"]),
        bm25_min_score=float(selected["bm25_min_score"]),
        vector_min_similarity=float(selected["vector_min_similarity"]),
        rrf_k=int(selected["rrf_k"]),
    )
    failures = _validation_failures(hybrid)
    validation_gate = _gate("retrieval", [(not failures, failure) for failure in failures])
    report = _base_report(
        run_level="validation", dataset=dataset, settings=configured,
        data_signature=data_signature, hybrid_configuration=selected,
        reranker_configuration=None, scenarios=[bm25, hybrid],
        requests=request_summary, failures=failures, version=calibration_version,
        quality_gates={"retrieval": validation_gate},
    )
    report.update({
        "pilot_artifact_sha256": sha256_file(pilot_path),
        "pilot_version": pilot.get("run_version"),
        "upstream_preflight": preflight,
        "thresholds_replayed_without_tuning": True,
        "validation_scope": "retrieval_only",
        "validation_authorized": False,
        "calibration_version": calibration_version if not failures else None,
    })
    _atomic_json(output, report)
    if not failures:
        registry[calibration_version] = {
            "created_at": report["generated_at"],
            "configuration_sha256": report["configuration_sha256"],
            "report_sha256": sha256_file(output),
            "embedding_recommendation": "candidate_default",
            "reranker_recommendation": "disabled",
        }
        _atomic_json(registry_path, registry)
    return report


def render_markdown(report: dict[str, Any]) -> str:
    gate = report.get("quality_gate") or {}
    hybrid_selection = report.get("hybrid_selection") or {}
    lines = [
        "# QASPER real retrieval benchmark", "",
        f"- Level / split: `{report['run_level']}` / `{report['split']}`",
        f"- Dataset / evaluated papers / cases: "
        f"{report.get('dataset_paper_count', report['paper_count'])} / "
        f"{report.get('evaluated_paper_count', report['paper_count'])} / {report['case_count']}",
        f"- Quality gate: `{'passed' if gate.get('passed') else 'failed'}`",
        f"- Validation authorization: `{report.get('validation_scope') or 'none'}`",
        f"- Production recommendation: embedding `{report['production_recommendation']['embedding']}`, reranker `{report['production_recommendation']['reranker']}`",
    ]
    if hybrid_selection:
        selected_proxy = hybrid_selection.get("selected_configuration_proxy_gate") or {}
        selected_cross_fold_proxy = hybrid_selection.get(
            "selected_configuration_cross_fold_proxy_gate"
        ) or {}
        lines += [
            f"- Hybrid selection: `{hybrid_selection.get('strategy')}`",
            f"- Retrieval-qualified / validation-proxy-qualified configurations: "
            f"{hybrid_selection.get('retrieval_qualified_configuration_count', 0)} / "
            f"{hybrid_selection.get('validation_proxy_qualified_configuration_count', 0)}",
            f"- All-fold validation-proxy-qualified configurations: "
            f"{hybrid_selection.get('cross_fold_validation_proxy_qualified_configuration_count', 0)}",
            f"- Selected configuration validation proxy: "
            f"`{'passed' if selected_proxy.get('passed') else 'failed'}`",
            f"- Selected configuration train cross-validation proxy: "
            f"`{'passed' if selected_cross_fold_proxy.get('passed') else 'failed'}`",
        ]
    if "validation_recommended" in report:
        lines.append(
            f"- Validation recommended: "
            f"`{'yes' if report['validation_recommended'] else 'no'}`"
        )
    lines += [
        "", "| Scenario | Cand. R@20 | R@6 | P@6 | MRR | Coverage | Evidence F1 | Refusal | False refusal | p95 ms |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for scenario in report["scenarios"]:
        metric = scenario["metrics"]
        lines.append(
            f"| {scenario['scenario']} | {metric['candidate_recall_at_20']:.3f} | "
            f"{metric['recall_at_6']:.3f} | {metric['precision_at_6']:.3f} | "
            f"{metric['mrr']:.3f} | {metric['evidence_coverage']:.3f} | "
            f"{metric['evidence_f1']:.3f} | {metric['unanswerable_refusal_rate']:.3f} | "
            f"{metric['answerable_false_refusal_rate']:.3f} | {metric['latency_p95_ms']:.1f} |"
        )
    if gate.get("failures"):
        lines += ["", "Gate failures:", ""] + [f"- {item}" for item in gate["failures"]]
    if report.get("quality_gates"):
        lines += ["", "Independent gates:", ""]
        lines += [
            f"- {name}: `{'passed' if item.get('passed') else 'failed'}`"
            for name, item in report["quality_gates"].items()
        ]
    lines += ["", "Only aggregate metrics and failure case IDs are persisted; questions and paper text are omitted.", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run real QASPER retrieval calibration")
    subparsers = parser.add_subparsers(dest="command", required=True)
    pilot = subparsers.add_parser("real-pilot")
    pilot.add_argument("--cache", type=Path, required=True)
    pilot.add_argument("--output", type=Path, required=True)
    pilot.add_argument("--pilot-version", required=True)
    pilot.add_argument("--checkpoint", type=Path)
    validation = subparsers.add_parser("real-validation")
    validation.add_argument("--cache", type=Path, required=True)
    validation.add_argument("--pilot", type=Path, required=True)
    validation.add_argument("--output", type=Path, required=True)
    validation.add_argument("--calibration-version", required=True)
    validation.add_argument("--checkpoint", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "real-pilot":
            result = run_real_pilot(
                args.cache, args.output, args.pilot_version,
                checkpoint_path=args.checkpoint,
            )
        else:
            result = run_real_validation(
                args.cache, args.pilot, args.output, args.calibration_version,
                checkpoint_path=args.checkpoint,
            )
        result["markdown"] = str(args.output.with_suffix(".md"))
        args.output.with_suffix(".md").write_text(render_markdown(result), encoding="utf-8")
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({
        "ok": True, "run_level": result["run_level"],
        "quality_gate_passed": result["quality_gate"]["passed"],
        "output": str(args.output),
    }, ensure_ascii=False))
    return 0 if result["quality_gate"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
