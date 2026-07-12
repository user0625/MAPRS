from __future__ import annotations

import argparse
import json
import math
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from typing import Any, Iterable

from backend.ask_paper import sanitize_citations
from backend.ask_retrieval import AskPaperRetrievalService
from backend.core.config import AppSettings
from backend.tools.embedder import BaseEmbedder

DEFAULT_DATASET = Path(__file__).parent / "fixtures" / "ask_paper_v1.json"


class OfflineSemanticEmbedder(BaseEmbedder):
    """Small deterministic concept embedder for the synthetic CI fixture."""

    model_name = "ask-paper-eval-v1"
    concepts = (
        ("car", "automobile", "vehicle", "semantic", "meaning", "dense", "语义", "向量"),
        ("bm25", "lexical", "term", "keyword", "关键词", "倒数"),
        ("fusion", "fuse", "combined", "combines", "rrf", "reciprocal", "合并", "融合"),
        ("recall", "rank", "quality", "coverage", "noise", "召回", "覆盖", "噪声", "质量", "指标"),
        ("latency", "p50", "p95", "percentile", "延迟", "分位"),
        ("section", "chapter", "constraint", "filter", "章节"),
        ("reranker", "rerank", "cross", "encoder", "database", "基线"),
    )

    def embed_text(self, text: str) -> list[float]:
        lowered = text.lower()
        def occurrences(term: str) -> int:
            if term.isascii():
                return len(re.findall(rf"(?<![a-z0-9_]){re.escape(term)}(?![a-z0-9_])", lowered))
            return lowered.count(term)

        vector = [float(sum(occurrences(term) for term in group)) for group in self.concepts]
        return vector if any(vector) else [0.0] * len(self.concepts)


class UnavailableEmbedder(BaseEmbedder):
    model_name = "unavailable-eval"

    def embed_text(self, text: str) -> list[float]:
        raise RuntimeError("offline embedding unavailable")


@dataclass(frozen=True)
class CaseObservation:
    case_id: str
    retrieved_chunk_ids: list[str]
    relevant_chunk_ids: list[str]
    allowed_evidence: list[str]
    requested_section: str | None
    retrieved_sections: list[str | None]
    should_refuse: bool
    refused: bool
    latency_ms: float
    degraded_reason: str | None = None
    bm25_candidates: int = 0
    vector_candidates_raw: int = 0
    vector_candidates_filtered: int = 0
    vector_candidates_removed: int = 0
    rrf_candidates: int = 0
    candidate_chunk_ids: list[str] | None = None
    language: str = "unknown"
    answerable: bool = True
    section_constrained: bool = False
    distractor_type: str = "unknown"


def percentile(values: Iterable[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def calculate_metrics(observations: list[CaseObservation]) -> dict[str, float]:
    if not observations:
        return {key: 0.0 for key in (
            "candidate_recall_at_20", "precision_at_6", "recall_at_6", "mrr", "evidence_coverage", "noise_rate",
            "section_boundary_rate", "no_answer_refusal_rate",
            "answerable_false_refusal_rate", "average_returned_count",
            "illegal_citation_retention_rate", "latency_p50_ms", "latency_p95_ms",
        )}
    unanswerable = [item for item in observations if item.should_refuse]
    recalls: list[float] = []
    candidate_recalls: list[float] = []
    precisions: list[float] = []
    reciprocal_ranks: list[float] = []
    covered = allowed = noise = retrieved = boundary = illegal_retained = illegal_attempted = 0
    for item in observations:
        relevant = set(item.relevant_chunk_ids)
        top = item.retrieved_chunk_ids[:6]
        if not item.should_refuse:
            recalls.append(len(relevant.intersection(top)) / len(relevant) if relevant else 0.0)
            candidates = (item.candidate_chunk_ids or item.retrieved_chunk_ids)[:20]
            candidate_recalls.append(len(relevant.intersection(candidates)) / len(relevant) if relevant else 0.0)
            precisions.append(len(relevant.intersection(top)) / len(top) if top else 0.0)
            reciprocal_ranks.append(next((1 / rank for rank, chunk_id in enumerate(top, 1) if chunk_id in relevant), 0.0))
            covered += len(set(item.allowed_evidence).intersection(top))
            allowed += len(set(item.allowed_evidence))
        noise += sum(chunk_id not in relevant for chunk_id in top)
        retrieved += len(top)
        if item.requested_section:
            boundary += sum(section != item.requested_section for section in item.retrieved_sections[:6])

        valid_id = "msg_eval:E1"
        invalid_ids = {"msg_other:E9", "msg_eval:E999"}
        answer, _ = sanitize_citations(
            f"valid [{valid_id}] invalid [msg_other:E9] [msg_eval:E999]", {valid_id}
        )
        illegal_attempted += len(invalid_ids)
        illegal_retained += sum(evidence_id in answer for evidence_id in invalid_ids)
    return {
        "candidate_recall_at_20": sum(candidate_recalls) / len(candidate_recalls) if candidate_recalls else 0.0,
        "precision_at_6": sum(precisions) / len(precisions) if precisions else 0.0,
        "recall_at_6": sum(recalls) / len(recalls) if recalls else 0.0,
        "mrr": sum(reciprocal_ranks) / len(reciprocal_ranks) if reciprocal_ranks else 0.0,
        "evidence_coverage": covered / allowed if allowed else 0.0,
        "noise_rate": noise / retrieved if retrieved else 0.0,
        "section_boundary_rate": boundary / retrieved if retrieved else 0.0,
        "no_answer_refusal_rate": sum(item.refused for item in unanswerable) / len(unanswerable) if unanswerable else 0.0,
        "answerable_false_refusal_rate": (
            sum(item.refused for item in observations if not item.should_refuse)
            / max(1, sum(not item.should_refuse for item in observations))
        ),
        "average_returned_count": retrieved / len(observations),
        "illegal_citation_retention_rate": illegal_retained / illegal_attempted if illegal_attempted else 0.0,
        "latency_p50_ms": median(item.latency_ms for item in observations),
        "latency_p95_ms": percentile((item.latency_ms for item in observations), 0.95),
    }


def _settings(mode: str) -> AppSettings:
    common = dict(
        _env_file=None, project_root=".", ask_candidate_count=20,
        ask_evidence_count=6, ask_rrf_k=60,
        # The deterministic fixture has a known cosine scale. Production keeps
        # the conservative zero-only default and can tune this model-specifically.
        ask_vector_min_similarity=1.0,
    )
    if mode == "bm25":
        return AppSettings(**common)
    return AppSettings(
        **common, embedding_provider="openai_compatible", embedding_model="ask-paper-eval-v1", embedding_api_key="offline"
    )


def evaluate(dataset_path: Path = DEFAULT_DATASET, mode: str = "bm25") -> dict[str, Any]:
    if mode not in {"bm25", "hybrid", "filtered-hybrid", "degraded"}:
        raise ValueError(f"unsupported mode: {mode}")
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    embedder: BaseEmbedder | None = None
    if mode in {"hybrid", "filtered-hybrid"}:
        embedder = OfflineSemanticEmbedder()
    elif mode == "degraded":
        embedder = UnavailableEmbedder()
    configured = _settings(mode)
    service = AskPaperRetrievalService(
        configured, embedder=embedder, filter_vector_candidates=mode != "hybrid"
    )
    observations: list[CaseObservation] = []
    with TemporaryDirectory(prefix="ask-paper-eval-") as directory:
        state_path = Path(directory) / "state.json"
        state_path.write_text(json.dumps({"document": {"chunks": dataset["chunks"]}}), encoding="utf-8")
        for case in dataset["cases"]:
            started = time.perf_counter_ns()
            result = service.retrieve("ask-paper-eval", str(state_path), case["query"], case.get("section"))
            latency_ms = (time.perf_counter_ns() - started) / 1_000_000
            chunks = [chunk for _, chunk in result.hits]
            observations.append(CaseObservation(
                case_id=case["id"],
                retrieved_chunk_ids=[str(chunk.get("chunk_id")) for chunk in chunks],
                relevant_chunk_ids=case["relevant_chunk_ids"],
                allowed_evidence=case["allowed_evidence"],
                requested_section=case.get("section"),
                retrieved_sections=[chunk.get("section") for chunk in chunks],
                should_refuse=case["should_refuse"],
                refused=not result.hits,
                latency_ms=latency_ms,
                degraded_reason=result.diagnostics.degraded_reason,
                bm25_candidates=result.diagnostics.bm25_candidates,
                vector_candidates_raw=result.diagnostics.vector_candidates_raw,
                vector_candidates_filtered=result.diagnostics.vector_candidates_filtered,
                vector_candidates_removed=result.diagnostics.vector_candidates_removed,
                rrf_candidates=result.diagnostics.rrf_candidates,
                candidate_chunk_ids=[
                    str(candidate["chunk_id"]) for candidate in result.diagnostics.candidate_scores
                ],
                language=case.get("language", "zh" if re.search(r"[\u3400-\u9fff]", case["query"]) else "en"),
                answerable=not case["should_refuse"],
                section_constrained=bool(case.get("section")),
                distractor_type=case.get("distractor_type", "unknown"),
            ))
    degraded_reasons = sorted({item.degraded_reason for item in observations if item.degraded_reason})
    effective_mode = "bm25" if mode == "degraded" and degraded_reasons else mode
    return {
        "dataset_version": dataset["version"],
        "requested_mode": mode,
        "effective_mode": effective_mode,
        "baseline_eligible": mode != "degraded" and not degraded_reasons,
        "degraded_reasons": degraded_reasons,
        "case_count": len(observations),
        "retrieval_config": {
            "candidate_limit": configured.ask_candidate_count,
            "evidence_limit": configured.ask_evidence_count,
            "rrf_k": configured.ask_rrf_k,
            "vector_filter_enabled": mode != "hybrid",
            "vector_min_similarity": (
                configured.ask_vector_min_similarity if mode != "hybrid" else None
            ),
        },
        "metrics": calculate_metrics(observations),
        "group_metrics": _group_metrics(observations),
        "candidate_totals": {
            "bm25": sum(item.bm25_candidates for item in observations),
            "vector_raw": sum(item.vector_candidates_raw for item in observations),
            "vector_filtered": sum(item.vector_candidates_filtered for item in observations),
            "vector_removed": sum(item.vector_candidates_removed for item in observations),
            "rrf": sum(item.rrf_candidates for item in observations),
        },
        "cases": [asdict(item) for item in observations],
    }


def _group_metrics(observations: list[CaseObservation]) -> dict[str, dict[str, dict[str, float]]]:
    """Expose slices so aggregate improvements cannot hide subgroup regressions."""
    dimensions = {
        "language": lambda item: item.language,
        "answerability": lambda item: "answerable" if not item.should_refuse else "unanswerable",
        "section_constraint": lambda item: "constrained" if item.section_constrained else "all",
        "distractor_type": lambda item: item.distractor_type,
    }
    output: dict[str, dict[str, dict[str, float]]] = {}
    for name, key in dimensions.items():
        groups: dict[str, list[CaseObservation]] = {}
        for item in observations:
            groups.setdefault(key(item), []).append(item)
        output[name] = {value: calculate_metrics(rows) for value, rows in groups.items()}
    return output


def compare_reports(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """Return filtered-hybrid deltas and acceptance failures against both baselines."""
    by_mode = {report["requested_mode"]: report for report in reports}
    required = {"bm25", "hybrid", "filtered-hybrid"}
    if not required <= by_mode.keys():
        return {}
    filtered = by_mode["filtered-hybrid"]["metrics"]
    raw = by_mode["hybrid"]["metrics"]
    bm25 = by_mode["bm25"]["metrics"]
    metric_names = (
        "recall_at_6", "mrr", "evidence_coverage", "noise_rate",
        "no_answer_refusal_rate", "latency_p50_ms", "latency_p95_ms",
    )
    failures = []
    if filtered["recall_at_6"] < raw["recall_at_6"]:
        failures.append("filtered-hybrid recall_at_6 is below hybrid")
    if filtered["noise_rate"] > bm25["noise_rate"]:
        failures.append("filtered-hybrid noise_rate is above bm25")
    if filtered["noise_rate"] >= raw["noise_rate"]:
        failures.append("filtered-hybrid noise_rate is not below hybrid")
    return {
        "filtered_minus_hybrid": {name: filtered[name] - raw[name] for name in metric_names},
        "filtered_minus_bm25": {name: filtered[name] - bm25[name] for name in metric_names},
        "acceptance_failures": failures,
    }


def quality_gate(report: dict[str, Any]) -> list[str]:
    metrics = report["metrics"]
    gates = {
        "recall_at_6": (metrics["recall_at_6"] >= 0.90, ">= 0.90"),
        "section_boundary_rate": (metrics["section_boundary_rate"] == 0.0, "== 0.0"),
        "illegal_citation_retention_rate": (metrics["illegal_citation_retention_rate"] == 0.0, "== 0.0"),
        "no_answer_refusal_rate": (metrics["no_answer_refusal_rate"] == 1.0, "== 1.0"),
    }
    return [f"{name}={metrics[name]:.4f} (expected {expected})" for name, (passed, expected) in gates.items() if not passed]


def _summary(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    status = "BASELINE" if report["baseline_eligible"] else "DEGRADED (excluded from hybrid baseline)"
    return (
        f"{report['requested_mode']}: {status}; effective={report['effective_mode']}; "
        f"Recall@6={metrics['recall_at_6']:.3f}, MRR={metrics['mrr']:.3f}, "
        f"coverage={metrics['evidence_coverage']:.3f}, noise={metrics['noise_rate']:.3f}, "
        f"boundary={metrics['section_boundary_rate']:.3f}, refusal={metrics['no_answer_refusal_rate']:.3f}, "
        f"illegal-citation={metrics['illegal_citation_retention_rate']:.3f}, "
        f"p50={metrics['latency_p50_ms']:.2f}ms, p95={metrics['latency_p95_ms']:.2f}ms"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic Ask Paper retrieval quality evaluation.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument(
        "--mode", choices=("bm25", "hybrid", "filtered-hybrid", "degraded", "all"), default="all"
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--gate", action="store_true", help="Fail if an eligible baseline misses a quality gate.")
    args = parser.parse_args()
    modes = ("bm25", "hybrid", "filtered-hybrid", "degraded") if args.mode == "all" else (args.mode,)
    reports = [evaluate(args.dataset, mode) for mode in modes]
    comparison = compare_reports(reports)
    payload = {"reports": reports, "comparison": comparison}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    for report in reports:
        print(_summary(report))
    # Raw hybrid is an intentionally noisy counterfactual. Apply hard gates to
    # the deployable baselines; its deltas are gated through the comparison.
    gated_modes = {"bm25", "filtered-hybrid"}
    failures = [
        failure
        for report in reports
        if report["baseline_eligible"] and report["requested_mode"] in gated_modes
        for failure in quality_gate(report)
    ]
    failures.extend(comparison.get("acceptance_failures", []))
    if args.gate and failures:
        print("Quality gate failed: " + "; ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
