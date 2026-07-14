from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from backend.core.config import AppSettings, get_settings
from backend.evaluation.calibrate import (
    BrokenEmbedder,
    BrokenReranker,
    ServiceFactory,
    _grid,
    _report,
    collect_raw_cases,
    hard_gate,
)
from backend.evaluation.real_dataset import EvaluationCase, ReviewStatus, Split, load_dataset, utc_now


PILOT_SCHEMA = "paper-pilot-gate-v1"
ACCEPTED_DECISION = "accept_for_pilot_only"


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
    service_factory: ServiceFactory | None = None,
) -> dict[str, Any]:
    if output.exists():
        raise ValueError("pilot output already exists")
    cases_path = directory / "cases.jsonl"
    before = _digest(cases_path)
    cases = derive_pilot_view(directory, adjudication_path)
    if not cases:
        raise ValueError("validation split has no pilot-accepted cases")
    settings = settings or get_settings()
    candidates = sorted({int(value) for value in candidate_counts})
    if not candidates or any(value < 1 or value > 100 for value in candidates):
        raise ValueError("candidate counts must be in [1, 100]")
    vectors, evidence, answerability = map(
        _grid, (vector_thresholds, evidence_thresholds, answerability_thresholds)
    )
    scored: list[tuple[tuple[float, ...], dict[str, Any], dict[str, Any], list[Any]]] = []
    scenarios: list[dict[str, Any]] = []
    for candidate_count in candidates:
        for vector_threshold in vectors:
            configured = settings.model_copy(update={
                "ask_candidate_count": candidate_count,
                "ask_vector_min_similarity": vector_threshold,
                "ask_reranker_mode": "shadow",
            })
            rows = collect_raw_cases(
                directory, cases, configured, service_factory=service_factory
            )
            shadow = _report(
                f"shadow-c{candidate_count}-v{vector_threshold:g}",
                rows,
                latency_with_reranker=True,
            )
            scenarios.append(_public_report(shadow))
            if any(not row.reranker_scores for row in rows):
                # A disabled/degraded reranker remains a supported pilot path.
                baseline = _report("reranker-disabled", rows)
                score = (float(not hard_gate(baseline)), baseline["metrics"]["precision_at_6"], baseline["metrics"]["recall_at_6"], baseline["metrics"]["mrr"], -baseline["metrics"]["latency_p95_ms"])
                scored.append((score, baseline, {"candidate_count": candidate_count, "vector_min_similarity": vector_threshold, "evidence_threshold": None, "answerability_threshold": None}, rows))
                continue
            for ev in evidence:
                for ans in answerability:
                    report = _report("offline-replay", rows, rerank=True, evidence_threshold=ev, answerability_threshold=ans)
                    failures = hard_gate(report)
                    metrics = report["metrics"]
                    score = (float(not failures), metrics["precision_at_6"], metrics["recall_at_6"], metrics["mrr"], -metrics["latency_p95_ms"])
                    scored.append((score, report, {"candidate_count": candidate_count, "vector_min_similarity": vector_threshold, "evidence_threshold": ev, "answerability_threshold": ans}, rows))
    if not scored:
        raise ValueError("pilot produced no evaluable scenario")
    _, selected, thresholds, _ = max(scored, key=lambda item: item[0])
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
        "selected_thresholds": thresholds,
        "selected_validation_report": _public_report(selected),
        "validation_gate_failures": failures,
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
    args = parser.parse_args()
    run_pilot_gate(args.directory, args.adjudication, args.output, args.pilot_version)


if __name__ == "__main__":
    main()
