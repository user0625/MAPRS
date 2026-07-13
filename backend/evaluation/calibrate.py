from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from backend.ask_retrieval import AskPaperRetrievalService
from backend.core.config import AppSettings, get_settings
from backend.evaluation.ask_paper import CaseObservation, calculate_metrics
from backend.evaluation.real_dataset import (
    DatasetManifest,
    EvaluationCase,
    PaperRecord,
    ReviewStatus,
    Split,
    ValidationPolicy,
    canonical_sha256,
    load_dataset,
    utc_now,
    validate_dataset,
)
from backend.reranker import BaseReranker
from backend.tools.embedder import BaseEmbedder


CALIBRATION_SCHEMA = "paper-calibration-v1"
REPORT_SCHEMA = "paper-frozen-report-v1"
TEST_RUN_FILE = "frozen-test-run.json"
CALIBRATION_REGISTRY = "calibration-versions.json"


class BrokenEmbedder(BaseEmbedder):
    model_name = "forced-unavailable"

    def embed_text(self, text: str) -> list[float]:
        raise RuntimeError("forced degradation")


class BrokenReranker(BaseReranker):
    model_name = "forced-unavailable"

    def score(self, query: str, passages: list[str], timeout: float) -> list[float]:
        raise TimeoutError("forced degradation")


@dataclass
class RawCase:
    case: EvaluationCase
    hybrid_ids: list[str]
    candidate_ids: list[str]
    candidate_sections: dict[str, str | None]
    reranker_scores: dict[str, float]
    latency_ms: float
    reranker_latency_ms: float
    degraded_reason: str | None
    bm25_candidates: int
    vector_candidates_raw: int
    vector_candidates_filtered: int
    vector_candidates_removed: int
    rrf_candidates: int


ServiceFactory = Callable[[AppSettings], AskPaperRetrievalService]


def _state_path(directory: Path, paper: PaperRecord) -> Path:
    path = Path(paper.state_path)
    return path if path.is_absolute() else directory / path


def config_signature(manifest: DatasetManifest, settings: AppSettings) -> dict[str, Any]:
    return {
        "dataset_version": manifest.dataset_version,
        "dataset_schema": manifest.schema_version,
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.embedding_model,
        "reranker_provider": settings.ask_reranker_provider,
        "reranker_model": settings.ask_reranker_model,
        "chunking_config": manifest.chunking_config,
        "candidate_count": settings.ask_candidate_count,
        "evidence_count": settings.ask_evidence_count,
        "rrf_k": settings.ask_rrf_k,
    }


def _eligible_cases(cases: Iterable[EvaluationCase], split: Split) -> list[EvaluationCase]:
    return [
        case for case in cases
        if case.split == split and case.review_status == ReviewStatus.REVIEWED
    ]


def collect_raw(
    directory: Path,
    split: Split,
    settings: AppSettings,
    *,
    service_factory: ServiceFactory | None = None,
    embedder: BaseEmbedder | None = None,
    reranker: BaseReranker | None = None,
) -> list[RawCase]:
    if split == Split.TEST:
        # Test access is intentionally isolated to run_frozen_test().
        raise ValueError("direct test evaluation is forbidden; use run_frozen_test")
    return _collect_raw(directory, split, settings, service_factory, embedder, reranker)


def _collect_raw(
    directory: Path,
    split: Split,
    settings: AppSettings,
    service_factory: ServiceFactory | None,
    embedder: BaseEmbedder | None,
    reranker: BaseReranker | None,
) -> list[RawCase]:
    _, papers, cases = load_dataset(directory)
    paper_map = {paper.paper_id: paper for paper in papers}
    services: dict[str, AskPaperRetrievalService] = {}
    rows: list[RawCase] = []
    for case in _eligible_cases(cases, split):
        paper = paper_map[case.paper_id]
        service = services.get(paper.paper_id)
        if service is None:
            service = (
                service_factory(settings) if service_factory
                else AskPaperRetrievalService(settings, embedder=embedder, reranker=reranker)
            )
            services[paper.paper_id] = service
        started = time.perf_counter()
        result = service.retrieve(
            paper.paper_id, str(_state_path(directory, paper)), case.question, case.section,
        )
        latency_ms = (time.perf_counter() - started) * 1000
        candidate_ids = [str(item["chunk_id"]) for item in result.diagnostics.candidate_scores]
        # State metadata is needed only in memory to check chapter boundaries.
        state = json.loads(_state_path(directory, paper).read_text(encoding="utf-8"))
        chunks = (state.get("document") or {}).get("chunks", [])
        sections = {str(item.get("chunk_id")): item.get("section") for item in chunks if isinstance(item, dict)}
        rerank = {
            str(item["chunk_id"]): float(item["reranker_score"])
            for item in result.diagnostics.candidate_scores
            if item.get("reranker_score") is not None
        }
        rows.append(RawCase(
            case=case,
            hybrid_ids=[str(chunk.get("chunk_id")) for _, chunk in result.hits],
            candidate_ids=candidate_ids,
            candidate_sections=sections,
            reranker_scores=rerank,
            latency_ms=latency_ms,
            reranker_latency_ms=result.diagnostics.reranker_latency_ms or 0.0,
            degraded_reason=result.diagnostics.degraded_reason,
            bm25_candidates=result.diagnostics.bm25_candidates,
            vector_candidates_raw=result.diagnostics.vector_candidates_raw,
            vector_candidates_filtered=result.diagnostics.vector_candidates_filtered,
            vector_candidates_removed=result.diagnostics.vector_candidates_removed,
            rrf_candidates=result.diagnostics.rrf_candidates,
        ))
    return rows


def _observations(
    rows: list[RawCase],
    *,
    rerank: bool,
    evidence_threshold: float = 0,
    answerability_threshold: float = 0,
    latency_with_reranker: bool = False,
) -> list[CaseObservation]:
    observations: list[CaseObservation] = []
    for row in rows:
        ids = row.hybrid_ids
        answerable = True
        if rerank:
            ranked = sorted(
                row.candidate_ids,
                key=lambda chunk_id: (-row.reranker_scores.get(chunk_id, -math.inf), row.candidate_ids.index(chunk_id)),
            )
            top_score = max(row.reranker_scores.values(), default=-math.inf)
            answerable = top_score >= answerability_threshold
            ids = (
                [chunk_id for chunk_id in ranked if row.reranker_scores.get(chunk_id, -math.inf) >= evidence_threshold][:6]
                if answerable else []
            )
        minimum = min(row.case.minimum_evidence_sets, key=len) if row.case.minimum_evidence_sets else []
        observations.append(CaseObservation(
            case_id=row.case.id,
            retrieved_chunk_ids=ids,
            relevant_chunk_ids=row.case.relevant_chunk_ids,
            allowed_evidence=minimum,
            requested_section=row.case.section,
            retrieved_sections=[row.candidate_sections.get(chunk_id) for chunk_id in ids],
            should_refuse=not row.case.answerable,
            refused=not answerable or not ids,
            latency_ms=(
                row.latency_ms if latency_with_reranker or rerank
                else max(0.0, row.latency_ms - row.reranker_latency_ms)
            ),
            degraded_reason=row.degraded_reason,
            bm25_candidates=row.bm25_candidates,
            vector_candidates_raw=row.vector_candidates_raw,
            vector_candidates_filtered=row.vector_candidates_filtered,
            vector_candidates_removed=row.vector_candidates_removed,
            rrf_candidates=row.rrf_candidates,
            candidate_chunk_ids=row.candidate_ids,
            language=row.case.language,
            answerable=row.case.answerable,
            section_constrained=bool(row.case.section),
            distractor_type=row.case.distractor_type.value,
        ))
    return observations


def _report(
    name: str,
    rows: list[RawCase],
    *,
    rerank: bool = False,
    evidence_threshold: float = 0,
    answerability_threshold: float = 0,
    latency_with_reranker: bool = False,
) -> dict[str, Any]:
    observations = _observations(
        rows, rerank=rerank, evidence_threshold=evidence_threshold,
        answerability_threshold=answerability_threshold,
        latency_with_reranker=latency_with_reranker,
    )
    metrics = calculate_metrics(observations)
    answerable_rows = [row for row in rows if row.case.answerable]
    if answerable_rows:
        retrieved_by_id = {item.case_id: set(item.retrieved_chunk_ids) for item in observations}
        metrics["evidence_coverage"] = sum(
            any(set(group) <= retrieved_by_id[row.case.id] for group in row.case.minimum_evidence_sets)
            for row in answerable_rows
        ) / len(answerable_rows)
    return {
        "scenario": name,
        "case_count": len(observations),
        "thresholds": {
            "evidence": evidence_threshold if rerank else None,
            "answerability": answerability_threshold if rerank else None,
        },
        "metrics": metrics,
        "degraded_reasons": sorted({row.degraded_reason for row in rows if row.degraded_reason}),
        "cases": [asdict(item) for item in observations],
    }


def hard_gate(report: dict[str, Any]) -> list[str]:
    metrics = report["metrics"]
    rules = {
        "candidate_recall_at_20": (metrics["candidate_recall_at_20"] >= 0.98, ">= 0.98"),
        "recall_at_6": (metrics["recall_at_6"] >= 0.95, ">= 0.95"),
        "precision_at_6": (metrics["precision_at_6"] >= 0.70, ">= 0.70"),
        "mrr": (metrics["mrr"] >= 0.85, ">= 0.85"),
        "evidence_coverage": (metrics["evidence_coverage"] >= 0.90, ">= 0.90"),
        "no_answer_refusal_rate": (metrics["no_answer_refusal_rate"] >= 0.90, ">= 0.90"),
        "answerable_false_refusal_rate": (metrics["answerable_false_refusal_rate"] <= 0.05, "<= 0.05"),
        "section_boundary_rate": (metrics["section_boundary_rate"] == 0, "== 0"),
        "illegal_citation_retention_rate": (metrics["illegal_citation_retention_rate"] == 0, "== 0"),
        "latency_p95_ms": (metrics["latency_p95_ms"] <= 1500, "<= 1500"),
    }
    return [
        f"{name}={metrics[name]:.4f} (expected {expected})"
        for name, (passed, expected) in rules.items() if not passed
    ]


def _grid(values: Iterable[float]) -> list[float]:
    result = sorted(set(float(value) for value in values))
    if not result or any(not 0 <= value <= 1 for value in result):
        raise ValueError("threshold grids must contain values in [0, 1]")
    return result


def calibrate(
    directory: Path,
    output: Path,
    calibration_version: str,
    *,
    settings: AppSettings | None = None,
    vector_thresholds: Iterable[float] = (0.0, 0.2, 0.4),
    evidence_thresholds: Iterable[float] = (0.0, 0.3, 0.5, 0.7),
    answerability_thresholds: Iterable[float] = (0.3, 0.5, 0.7),
    service_factory: ServiceFactory | None = None,
) -> dict[str, Any]:
    if output.exists():
        raise ValueError("calibration output already exists; use a new calibration version/path")
    validate_dataset(directory, ValidationPolicy.fixture())
    manifest, _, cases = load_dataset(directory)
    if not _eligible_cases(cases, Split.VALIDATION):
        raise ValueError("validation split has no reviewed cases")
    settings = settings or get_settings()
    registry_path = directory / CALIBRATION_REGISTRY
    registry = json.loads(registry_path.read_text(encoding="utf-8")) if registry_path.exists() else {}
    if calibration_version in registry:
        raise ValueError("calibration version was already used; choose a new calibration version")
    if not settings.ask_reranker_model and service_factory is None:
        raise ValueError("reranker model is required for calibration")
    vectors, evidence, answerability = map(_grid, (vector_thresholds, evidence_thresholds, answerability_thresholds))
    vectors = sorted(set(vectors + [settings.ask_vector_min_similarity]))
    scenarios: list[dict[str, Any]] = []
    candidates: list[tuple[tuple[float, ...], dict[str, Any], dict[str, float]]] = []
    bm25_settings = settings.model_copy(update={"embedding_provider": "mock", "ask_reranker_mode": "disabled"})
    bm25_rows = collect_raw(directory, Split.VALIDATION, bm25_settings, service_factory=service_factory)
    best_non_rerank: dict[str, Any] | None = _report("bm25", bm25_rows)
    scenarios.append(best_non_rerank)
    raw_by_vector: dict[float, list[RawCase]] = {}
    for vector_threshold in vectors:
        shadow_settings = settings.model_copy(update={
            "ask_vector_min_similarity": vector_threshold,
            "ask_reranker_mode": "shadow",
        })
        rows = collect_raw(directory, Split.VALIDATION, shadow_settings, service_factory=service_factory)
        raw_by_vector[vector_threshold] = rows
        label = "hybrid-default" if vector_threshold == settings.ask_vector_min_similarity else f"hybrid-tuned-{vector_threshold:g}"
        hybrid = _report(label, rows)
        scenarios.append(hybrid)
        if best_non_rerank is None or (
            hybrid["metrics"]["precision_at_6"], hybrid["metrics"]["recall_at_6"]
        ) > (
            best_non_rerank["metrics"]["precision_at_6"], best_non_rerank["metrics"]["recall_at_6"]
        ):
            best_non_rerank = hybrid
        if any(not row.reranker_scores for row in rows):
            continue
        for evidence_threshold in evidence:
            for answerability_threshold in answerability:
                report = _report(
                    "reranker-enabled", rows, rerank=True,
                    evidence_threshold=evidence_threshold,
                    answerability_threshold=answerability_threshold,
                )
                report["vector_threshold"] = vector_threshold
                failures = hard_gate(report)
                # Feasible candidates win; then optimize precision, recall, MRR, and latency.
                score = (
                    float(not failures), report["metrics"]["precision_at_6"],
                    report["metrics"]["recall_at_6"], report["metrics"]["mrr"],
                    -report["metrics"]["latency_p95_ms"],
                )
                candidates.append((score, report, {
                    "vector_min_similarity": vector_threshold,
                    "evidence_threshold": evidence_threshold,
                    "answerability_threshold": answerability_threshold,
                }))
    if not candidates or best_non_rerank is None:
        raise ValueError("no complete reranker scores were produced")
    _, selected, thresholds = max(candidates, key=lambda item: item[0])
    selected_shadow = _report(
        "reranker-shadow", raw_by_vector[thresholds["vector_min_similarity"]],
        latency_with_reranker=True,
    )
    scenarios.append(selected_shadow)
    selected["scenario"] = "reranker-enabled"
    scenarios.append(selected)
    uplift = selected["metrics"]["precision_at_6"] - best_non_rerank["metrics"]["precision_at_6"]
    failures = hard_gate(selected)
    production_mode = "enabled" if uplift >= 0.10 and not failures else "disabled"

    # Explicit degradation scenarios are measured once and never selected as baselines.
    if service_factory is None:
        degraded_embedding_settings = settings.model_copy(update={
            "embedding_provider": "openai_compatible", "ask_reranker_mode": "disabled",
        })
        scenarios.append(_report(
            "embedding-degraded",
            collect_raw(directory, Split.VALIDATION, degraded_embedding_settings, embedder=BrokenEmbedder()),
        ))
        degraded_rerank_settings = settings.model_copy(update={"ask_reranker_mode": "enabled"})
        scenarios.append(_report(
            "reranker-degraded",
            collect_raw(directory, Split.VALIDATION, degraded_rerank_settings, reranker=BrokenReranker()),
            latency_with_reranker=True,
        ))
    artifact = {
        "schema_version": CALIBRATION_SCHEMA,
        "calibration_version": calibration_version,
        "created_at": utc_now(),
        "split_used": "validation",
        "config_signature": config_signature(manifest, settings),
        "selected_thresholds": thresholds,
        "selected_validation_report": selected,
        "best_non_rerank_validation_report": best_non_rerank,
        "precision_uplift": uplift,
        "validation_gate_failures": failures,
        "recommended_production_mode": production_mode,
        "scenarios": scenarios,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    registry[calibration_version] = {
        "created_at": artifact["created_at"],
        "config_sha256": canonical_sha256([artifact["config_signature"]]),
        "artifact_path": str(output.resolve()),
    }
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return artifact


def run_frozen_test(
    directory: Path,
    calibration_path: Path,
    output: Path,
    *,
    settings: AppSettings | None = None,
    service_factory: ServiceFactory | None = None,
) -> dict[str, Any]:
    if output.exists() or (directory / TEST_RUN_FILE).exists():
        raise ValueError("frozen test has already been run for this dataset")
    validate_dataset(directory, ValidationPolicy.fixture())
    manifest, _, cases = load_dataset(directory)
    if not manifest.test_frozen:
        raise ValueError("test split must be frozen before the quality gate")
    if not _eligible_cases(cases, Split.TEST):
        raise ValueError("test split has no reviewed cases")
    artifact = json.loads(calibration_path.read_text(encoding="utf-8"))
    if artifact.get("schema_version") != CALIBRATION_SCHEMA or artifact.get("split_used") != "validation":
        raise ValueError("invalid calibration artifact")
    settings = settings or get_settings()
    if artifact.get("config_signature") != config_signature(manifest, settings):
        raise ValueError("model, chunking, or dataset version changed; create a new calibration version")
    thresholds = artifact["selected_thresholds"]
    enabled_settings = settings.model_copy(update={
        "ask_vector_min_similarity": thresholds["vector_min_similarity"],
        "ask_evidence_threshold": thresholds["evidence_threshold"],
        "ask_answerability_threshold": thresholds["answerability_threshold"],
        "ask_reranker_mode": "shadow",  # collect once; enabled behavior is replayed below
        "ask_calibration_version": artifact["calibration_version"],
    })
    rows = _collect_raw(
        directory, Split.TEST, enabled_settings, service_factory, None, None,
    )
    candidate = _report(
        "frozen-candidate", rows, rerank=True,
        evidence_threshold=thresholds["evidence_threshold"],
        answerability_threshold=thresholds["answerability_threshold"],
    )
    baseline = _report("frozen-best-no-rerank", rows)
    uplift = candidate["metrics"]["precision_at_6"] - baseline["metrics"]["precision_at_6"]
    failures = hard_gate(candidate)
    if uplift < 0.10:
        failures.append(f"reranker precision uplift={uplift:.4f} (expected >= 0.10)")
    report = {
        "schema_version": REPORT_SCHEMA,
        "report_version": f"{manifest.dataset_version}:{artifact['calibration_version']}",
        "created_at": utc_now(),
        "dataset_version": manifest.dataset_version,
        "frozen_test_sha256": manifest.frozen_test_sha256,
        "calibration_version": artifact["calibration_version"],
        "config_signature": artifact["config_signature"],
        "selected_thresholds": thresholds,
        "candidate": candidate,
        "best_non_rerank": baseline,
        "precision_uplift": uplift,
        "quality_gate_passed": not failures,
        "quality_gate_failures": failures,
        "production_mode": "enabled" if not failures else "disabled",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    output.write_text(serialized, encoding="utf-8")
    ledger = {
        "dataset_version": manifest.dataset_version,
        "calibration_version": artifact["calibration_version"],
        "run_at": report["created_at"],
        "report_sha256": canonical_sha256([report]),
        "quality_gate_passed": report["quality_gate_passed"],
    }
    (directory / TEST_RUN_FILE).write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    return report


def _csv_floats(value: str) -> list[float]:
    return [float(item) for item in value.split(",") if item.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Calibrate thresholds on validation or run the frozen quality gate once.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    calibration = subparsers.add_parser("calibrate")
    calibration.add_argument("--dataset", type=Path, required=True)
    calibration.add_argument("--output", type=Path, required=True)
    calibration.add_argument("--calibration-version", required=True)
    calibration.add_argument("--vector-thresholds", type=_csv_floats, default=[0, 0.2, 0.4])
    calibration.add_argument("--evidence-thresholds", type=_csv_floats, default=[0, 0.3, 0.5, 0.7])
    calibration.add_argument("--answerability-thresholds", type=_csv_floats, default=[0.3, 0.5, 0.7])
    frozen = subparsers.add_parser("frozen-test")
    frozen.add_argument("--dataset", type=Path, required=True)
    frozen.add_argument("--calibration", type=Path, required=True)
    frozen.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "calibrate":
            result = calibrate(
                args.dataset, args.output, args.calibration_version,
                vector_thresholds=args.vector_thresholds,
                evidence_thresholds=args.evidence_thresholds,
                answerability_thresholds=args.answerability_thresholds,
            )
            summary = {
                "calibration_version": result["calibration_version"],
                "recommended_production_mode": result["recommended_production_mode"],
                "selected_thresholds": result["selected_thresholds"],
            }
            exit_code = 0
        else:
            result = run_frozen_test(args.dataset, args.calibration, args.output)
            summary = {"report_version": result["report_version"], "passed": result["quality_gate_passed"]}
            exit_code = 0 if result["quality_gate_passed"] else 1
    except (ValueError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": True, **summary}, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
