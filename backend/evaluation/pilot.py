from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from backend.core.config import AppSettings, get_settings
from backend.ask_retrieval import AskPaperRetrievalService
from backend.evaluation.calibrate import (
    BrokenEmbedder,
    BrokenReranker,
    RawCase,
    ServiceFactory,
    _grid,
    _report,
    collect_raw_cases,
    hard_gate,
)
from backend.evaluation.real_dataset import EvaluationCase, ReviewStatus, Split, load_dataset, utc_now
from backend.evaluation.upstream_preflight import run_preflight
from backend.tools.embedder import BaseEmbedder


PILOT_SCHEMA = "paper-pilot-gate-v2"
ACCEPTED_DECISION = "accept_for_pilot_only"


class _MemoizedEmbedder(BaseEmbedder):
    """Ensure grid replay never repeats an upstream embedding for the same text."""

    def __init__(self, delegate: BaseEmbedder) -> None:
        self.delegate = delegate
        self.model_name = delegate.model_name
        self.cache: dict[str, list[float]] = {}

    def embed_text(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        missing = list(dict.fromkeys(text for text in texts if text not in self.cache))
        if missing:
            vectors = self.delegate.embed_texts(missing)
            if len(vectors) != len(missing):
                raise ValueError("embedding count mismatch")
            self.cache.update(zip(missing, vectors))
        return [self.cache[text] for text in texts]

    def embed_query(self, query: str) -> list[float]:
        if query not in self.cache:
            self.cache[query] = self.delegate.embed_query(query)
        return self.cache[query]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def derive_pilot_view(
    directory: Path,
    adjudication_path: Path,
    *,
    split: Split = Split.VALIDATION,
) -> list[EvaluationCase]:
    """Build a read-only expert-approved view without modifying gold files."""
    if split == Split.TEST:
        raise ValueError("pilot gate is forbidden from accessing the test split")
    _, _, cases = load_dataset(directory)
    decisions: dict[str, str] = {}
    for line in adjudication_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        # Deliberately discard test adjudications before indexing them.
        if row.get("split") == Split.TEST.value:
            continue
        decisions[str(row["case_id"])] = str(row.get("expert_decision", ""))
    return [
        case.model_copy(update={"review_status": ReviewStatus.REVIEWED})
        for case in cases
        if case.split == split and decisions.get(case.id) == ACCEPTED_DECISION
    ]


def _failure_dimensions(report: dict[str, Any], cases: list[EvaluationCase]) -> dict[str, Any]:
    """Return aggregate-only failure attribution; never emit questions or text."""
    case_by_id = {case.id: case for case in cases}
    metric_failures = [item.split("=", 1)[0] for item in hard_gate(report)]
    by_paper: Counter[str] = Counter()
    by_language: Counter[str] = Counter()
    by_answerability: Counter[str] = Counter()
    by_distractor: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()

    for observation in report.get("cases", []):
        case = case_by_id.get(str(observation.get("case_id")))
        if case is None:
            continue
        retrieved = set(observation.get("retrieved_chunk_ids") or [])
        relevant = set(observation.get("relevant_chunk_ids") or [])
        reasons: list[str] = []
        if case.answerable and not (retrieved & relevant):
            reasons.append("relevant_evidence_missed")
        if case.answerable and observation.get("refused"):
            reasons.append("false_refusal")
        if not case.answerable and not observation.get("refused"):
            reasons.append("no_answer_not_refused")
        if case.section and any(
            section and section.casefold() != case.section.casefold()
            for section in observation.get("retrieved_sections") or []
        ):
            reasons.append("section_boundary")
        if not reasons:
            continue
        by_paper[case.paper_id] += 1
        by_language[case.language] += 1
        by_answerability["answerable" if case.answerable else "unanswerable"] += 1
        by_distractor[case.distractor_type.value] += 1
        reason_counts.update(reasons)
    return {
        "metrics": metric_failures,
        "paper": dict(sorted(by_paper.items())),
        "language": dict(sorted(by_language.items())),
        "answerability": dict(sorted(by_answerability.items())),
        "distractor_type": dict(sorted(by_distractor.items())),
        "failure_reason": dict(sorted(reason_counts.items())),
    }


def _public_report(report: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in report.items() if key != "cases"}


def _observed_thresholds(fixed: Iterable[float], scores: Iterable[float]) -> list[float]:
    """Combine fixed boundaries with observed-score boundaries and midpoints."""
    values = _grid(fixed)
    observed = sorted({float(value) for value in scores if 0 <= float(value) <= 1})
    result = set(values) | {0.0, 1.0} | set(observed)
    result.update((left + right) / 2 for left, right in zip(observed, observed[1:]))
    return sorted(result)


def _positive_floats(values: Iterable[float], name: str) -> list[float]:
    result = sorted({float(value) for value in values})
    if not result or any(value <= 0 for value in result):
        raise ValueError(f"{name} must contain positive values")
    return result


def _csv_floats(value: str) -> list[float]:
    return [float(item) for item in value.split(",") if item.strip()]


def _csv_ints(value: str) -> list[int]:
    return [int(item) for item in value.split(",") if item.strip()]


def _prepare_lexical_replay(
    directory: Path, rows: list[RawCase]
) -> tuple[dict[str, Any], dict[str, dict[str, str | None]]]:
    _, papers, _ = load_dataset(directory)
    paper_map = {paper.paper_id: paper for paper in papers}
    indexes: dict[str, Any] = {}
    sections: dict[str, dict[str, str | None]] = {}
    state_cache: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        chunks = state_cache.get(row.case.paper_id)
        if chunks is None:
            paper = paper_map[row.case.paper_id]
            path = Path(paper.state_path)
            if not path.is_absolute():
                path = directory / path
            state = json.loads(path.read_text(encoding="utf-8"))
            chunks = [
                item
                for item in (state.get("document") or {}).get("chunks", [])
                if isinstance(item, dict)
            ]
            state_cache[row.case.paper_id] = chunks
        scoped = [
            chunk for chunk in chunks
            if not row.case.section or chunk.get("section") == row.case.section
        ]
        indexes[row.case.id] = AskPaperRetrievalService._build_lexical(scoped)
        sections[row.case.id] = {
            str(chunk.get("chunk_id")): chunk.get("section") for chunk in scoped
        }
    return indexes, sections


def _replay_candidates(
    rows: list[RawCase],
    indexes: dict[str, Any],
    sections: dict[str, dict[str, str | None]],
    settings: AppSettings,
) -> list[RawCase]:
    replayed: list[RawCase] = []
    for row in rows:
        index = indexes[row.case.id]
        positions = {
            str(chunk.get("chunk_id")): position
            for position, chunk in enumerate(index.chunks)
        }
        lexical = AskPaperRetrievalService.bm25(
            index,
            row.case.question,
            settings.ask_candidate_count,
            settings.ask_bm25_k1,
            settings.ask_bm25_b,
        )
        lexical_ids = [
            (str(index.chunks[position].get("chunk_id")), score)
            for position, score in lexical
        ]
        vector_raw = [
            (str(item.get("chunk_id")), float(item.get("score")))
            for item in (row.vector_score_rows or [])[: settings.ask_candidate_count]
            if str(item.get("chunk_id")) in positions
            and isinstance(item.get("score"), (int, float))
        ]
        vector = [
            (chunk_id, score)
            for chunk_id, score in vector_raw
            if math.isfinite(score)
            and score > 0
            and score >= settings.ask_vector_min_similarity
        ]
        fused: dict[str, float] = {}
        for ranked in (lexical_ids, vector):
            for rank, (chunk_id, _) in enumerate(ranked, 1):
                fused[chunk_id] = fused.get(chunk_id, 0.0) + 1 / (settings.ask_rrf_k + rank)
        candidate_ids = [
            chunk_id
            for chunk_id, _ in sorted(
                fused.items(), key=lambda item: (-item[1], positions[item[0]])
            )[: settings.ask_candidate_count]
        ]
        replayed.append(RawCase(
            case=row.case,
            hybrid_ids=candidate_ids[: settings.ask_evidence_count],
            candidate_ids=candidate_ids,
            candidate_sections=sections[row.case.id],
            reranker_scores={},
            latency_ms=row.latency_ms,
            reranker_latency_ms=0.0,
            degraded_reason=row.degraded_reason,
            bm25_candidates=len(lexical_ids),
            vector_candidates_raw=len(vector_raw),
            vector_candidates_filtered=len(vector),
            vector_candidates_removed=len(vector_raw) - len(vector),
            rrf_candidates=len(fused),
            index_build_ms=row.index_build_ms,
            index_load_ms=row.index_load_ms,
            index_cache_hit=row.index_cache_hit,
            index_memory_cache_hit=row.index_memory_cache_hit,
            index_cold_build_failed=row.index_cold_build_failed,
            bm25_score_rows=[
                {"chunk_id": chunk_id, "score": score, "rank": rank}
                for rank, (chunk_id, score) in enumerate(lexical_ids, 1)
            ],
            vector_score_rows=row.vector_score_rows,
        ))
    return replayed


def run_pilot_gate(
    directory: Path,
    adjudication_path: Path,
    output: Path,
    pilot_version: str,
    *,
    settings: AppSettings | None = None,
    candidate_counts: Iterable[int] = (20, 30, 40),
    vector_thresholds: Iterable[float] = (0.0, 0.2, 0.4),
    evidence_thresholds: Iterable[float] = (0.0, 0.3, 0.5, 0.7),
    answerability_thresholds: Iterable[float] = (0.3, 0.5, 0.7),
    bm25_k1_values: Iterable[float] | None = None,
    bm25_b_values: Iterable[float] | None = None,
    rrf_k_values: Iterable[int] | None = None,
    service_factory: ServiceFactory | None = None,
    require_preflight: bool = True,
) -> dict[str, Any]:
    if output.exists():
        raise ValueError("pilot output already exists")
    cases_path = directory / "cases.jsonl"
    before = _digest(cases_path)
    cases = derive_pilot_view(directory, adjudication_path)
    if not cases:
        raise ValueError("validation split has no pilot-accepted cases")
    settings = settings or get_settings()
    preflight: dict[str, Any] | None = None
    if require_preflight and service_factory is None:
        preflight = run_preflight(settings)
        if not preflight["ok"]:
            failures = [
                {
                    "service": check["service"],
                    "category": check["category"],
                    "configuration": check["configuration"],
                }
                for check in preflight["checks"]
                if not check["ok"]
            ]
            raise ValueError(f"upstream preflight failed: {json.dumps(failures, separators=(',', ':'))}")
    effective_factory = service_factory
    if effective_factory is None:
        bootstrap = AskPaperRetrievalService(settings)
        memoized = _MemoizedEmbedder(bootstrap._embedder())
        shared_reranker = bootstrap._reranker()

        def effective_factory(configured: AppSettings) -> AskPaperRetrievalService:
            return AskPaperRetrievalService(
                configured, embedder=memoized, reranker=shared_reranker
            )
    candidates = sorted({int(value) for value in candidate_counts})
    if not candidates or any(value < 1 or value > 100 for value in candidates):
        raise ValueError("candidate counts must be in [1, 100]")
    vectors = _grid(vector_thresholds)
    bm25_k1s = _positive_floats(
        bm25_k1_values or (settings.ask_bm25_k1,), "BM25 k1 grid"
    )
    bm25_bs = sorted({float(value) for value in (bm25_b_values or (settings.ask_bm25_b,))})
    if not bm25_bs or any(not 0 <= value <= 1 for value in bm25_bs):
        raise ValueError("BM25 b grid must contain values in [0, 1]")
    rrf_ks = sorted({int(value) for value in (rrf_k_values or (settings.ask_rrf_k,))})
    if not rrf_ks or any(value < 1 or value > 1000 for value in rrf_ks):
        raise ValueError("RRF k grid must contain values in [1, 1000]")
    recall_candidates: list[tuple[tuple[float, ...], dict[str, Any], dict[str, Any], list[Any]]] = []
    scenarios: list[dict[str, Any]] = []
    collection_settings = settings.model_copy(update={
        "ask_candidate_count": max(20, max(candidates)),
        "ask_vector_min_similarity": -1.0,
        "ask_reranker_mode": "disabled",
    })
    collected_rows = collect_raw_cases(
        directory, cases, collection_settings, service_factory=effective_factory
    )
    lexical_indexes, section_maps = _prepare_lexical_replay(directory, collected_rows)
    # Every candidate grid is replayed from the one maximum-depth collection.
    # Only the selected recall configuration is sent through one reranker shadow.
    for k1 in bm25_k1s:
        for b in bm25_bs:
            for rrf_k in rrf_ks:
                for candidate_count in candidates:
                    for vector_threshold in vectors:
                        configured = settings.model_copy(update={
                            "ask_bm25_k1": k1,
                            "ask_bm25_b": b,
                            "ask_rrf_k": rrf_k,
                            "ask_candidate_count": candidate_count,
                            "ask_vector_min_similarity": vector_threshold,
                            "ask_reranker_mode": "disabled",
                        })
                        rows = _replay_candidates(
                            collected_rows, lexical_indexes, section_maps, configured
                        )
                        report = _report(
                            f"candidate-k1-{k1:g}-b-{b:g}-rrf-{rrf_k}-c-{candidate_count}-v-{vector_threshold:g}",
                            rows,
                        )
                        scenarios.append(_public_report(report))
                        metrics = report["metrics"]
                        score = (
                            metrics["candidate_recall_at_20"],
                            metrics["recall_at_6"],
                            metrics["precision_at_6"],
                            metrics["mrr"],
                            -metrics["latency_p95_ms"],
                        )
                        recall_candidates.append((score, report, {
                            "bm25_k1": k1,
                            "bm25_b": b,
                            "rrf_k": rrf_k,
                            "candidate_count": candidate_count,
                            "vector_min_similarity": vector_threshold,
                        }, rows))
    if not recall_candidates:
        raise ValueError("pilot produced no evaluable scenario")
    _, candidate_report, retrieval_config, _ = max(recall_candidates, key=lambda item: item[0])
    shadow_settings = settings.model_copy(update={
        "ask_bm25_k1": retrieval_config["bm25_k1"],
        "ask_bm25_b": retrieval_config["bm25_b"],
        "ask_rrf_k": retrieval_config["rrf_k"],
        "ask_candidate_count": retrieval_config["candidate_count"],
        "ask_vector_min_similarity": retrieval_config["vector_min_similarity"],
        "ask_reranker_mode": "shadow",
    })
    shadow_rows = collect_raw_cases(
        directory, cases, shadow_settings, service_factory=effective_factory
    )
    shadow = _report("selected-single-reranker-shadow", shadow_rows, latency_with_reranker=True)
    scenarios.append(_public_report(shadow))
    all_scores = [score for row in shadow_rows for score in row.reranker_scores.values()]
    top_scores = [max(row.reranker_scores.values()) for row in shadow_rows if row.reranker_scores]
    scored: list[tuple[tuple[float, ...], dict[str, Any], dict[str, Any]]] = []
    if all(row.reranker_scores for row in shadow_rows):
        evidence = _observed_thresholds(evidence_thresholds, all_scores)
        answerability = _observed_thresholds(answerability_thresholds, top_scores)
        for ev in evidence:
            for ans in answerability:
                report = _report(
                    "offline-threshold-replay", shadow_rows, rerank=True,
                    evidence_threshold=ev, answerability_threshold=ans,
                )
                metric = report["metrics"]
                score = (
                    float(not hard_gate(report)), metric["precision_at_6"],
                    metric["recall_at_6"], metric["mrr"], -metric["latency_p95_ms"],
                )
                scored.append((score, report, {
                    **retrieval_config,
                    "evidence_threshold": ev,
                    "answerability_threshold": ans,
                }))
    if scored:
        _, selected, thresholds = max(scored, key=lambda item: item[0])
    else:
        selected = candidate_report
        thresholds = {
            **retrieval_config,
            "evidence_threshold": None,
            "answerability_threshold": None,
        }
    failures = hard_gate(selected)

    degradation: list[dict[str, Any]] = []
    if service_factory is None:
        base = settings.model_copy(update={"ask_reranker_mode": "disabled", "embedding_provider": "openai_compatible"})
        degradation.append(_public_report(_report("embedding-degraded", collect_raw_cases(directory, cases, base, embedder=BrokenEmbedder()))))
        base = settings.model_copy(update={"ask_reranker_mode": "enabled"})
        degradation.append(_public_report(_report("reranker-degraded", collect_raw_cases(directory, cases, base, reranker=BrokenReranker()), latency_with_reranker=True)))

    degradation_paths_passed = (
        service_factory is not None
        or (
            {item["scenario"] for item in degradation}
            == {"embedding-degraded", "reranker-degraded"}
            and all(item["case_count"] == len(cases) for item in degradation)
        )
    )

    artifact = {
        "schema_version": PILOT_SCHEMA,
        "pilot_version": pilot_version,
        "created_at": utc_now(),
        "evidence_grade": "pilot_only",
        "quality_conclusion": "engineering_admission_only_not_formal_quality",
        "splits_used": ["validation"],
        "test_accessed": False,
        "source_cases_mutated": False,
        "pilot_case_count": len(cases),
        "upstream_preflight": preflight,
        "candidate_selection_report": _public_report(candidate_report),
        "candidate_collection_count": 1,
        "reranker_shadow_count": 1,
        "selected_thresholds": thresholds,
        "selected_validation_report": _public_report(selected),
        "validation_gate_failures": failures,
        "selection_feasible": not failures,
        "degradation_paths_passed": degradation_paths_passed,
        "exit_ready_for_comparison_mvp": not failures and degradation_paths_passed,
        "failure_attribution": _failure_dimensions(selected, cases),
        "degradation_scenarios": degradation,
        "scenarios": scenarios,
        "reranker_mode": "disabled",
        "production_enablement_recommendation": None,
        "notices": [
            "Pilot-only evidence is not formal human-reviewed gold.",
            "The frozen test split was not accessed or modified.",
            "Passing this gate must not enable the reranker in production.",
        ],
    }
    if _digest(cases_path) != before:
        raise RuntimeError("source cases changed during pilot evaluation")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return artifact


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the pilot-only validation gate")
    parser.add_argument("directory", type=Path)
    parser.add_argument("adjudication", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("pilot_version")
    parser.add_argument("--candidate-counts", type=_csv_ints, default=[20, 30, 40])
    parser.add_argument("--vector-thresholds", type=_csv_floats, default=[0, 0.2, 0.4])
    parser.add_argument("--evidence-thresholds", type=_csv_floats, default=[0, 0.3, 0.5, 0.7])
    parser.add_argument("--answerability-thresholds", type=_csv_floats, default=[0.3, 0.5, 0.7])
    parser.add_argument("--bm25-k1", type=_csv_floats, default=None)
    parser.add_argument("--bm25-b", type=_csv_floats, default=None)
    parser.add_argument("--rrf-k", type=_csv_ints, default=None)
    args = parser.parse_args()
    run_pilot_gate(
        args.directory, args.adjudication, args.output, args.pilot_version,
        candidate_counts=args.candidate_counts,
        vector_thresholds=args.vector_thresholds,
        evidence_thresholds=args.evidence_thresholds,
        answerability_thresholds=args.answerability_thresholds,
        bm25_k1_values=args.bm25_k1,
        bm25_b_values=args.bm25_b,
        rrf_k_values=args.rrf_k,
    )


if __name__ == "__main__":
    main()
