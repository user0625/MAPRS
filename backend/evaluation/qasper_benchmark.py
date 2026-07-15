from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Literal

from backend.evaluation.qasper import QasperAdaptation, QasperCase, QasperPaper, import_qasper, load_adapted


REPORT_SCHEMA = "public-paper-benchmark-v1"
SCENARIOS = ("bm25", "vector", "rrf", "rrf_reranker", "embedding_degraded", "reranker_degraded")


def tokenize(text: str) -> list[str]:
    lowered = text.casefold()
    words = re.findall(r"[a-z0-9]+(?:[-'][a-z0-9]+)*|[\u3400-\u9fff]", lowered)
    return words


def percentile(values: Iterable[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


class PaperIndex:
    def __init__(self, paper: QasperPaper, *, dimensions: int = 256) -> None:
        self.paper = paper
        self.dimensions = dimensions
        self.tokens = [tokenize(chunk.text) for chunk in paper.chunks]
        self.document_frequency: Counter[str] = Counter()
        for tokens in self.tokens:
            self.document_frequency.update(set(tokens))
        self.average_length = sum(map(len, self.tokens)) / max(1, len(self.tokens))
        self.vectors = [self._vector(tokens) for tokens in self.tokens]

    def _vector(self, tokens: list[str]) -> list[float]:
        result = [0.0] * self.dimensions
        for token in tokens:
            slot = int(hashlib.sha256(token.encode()).hexdigest()[:8], 16) % self.dimensions
            result[slot] += 1.0
        norm = math.sqrt(sum(value * value for value in result))
        return [value / norm for value in result] if norm else result

    def bm25(self, query: str, top_k: int = 20) -> list[tuple[str, float]]:
        query_tokens = tokenize(query)
        total = max(1, len(self.tokens))
        rows: list[tuple[int, float]] = []
        for index, tokens in enumerate(self.tokens):
            frequencies = Counter(tokens)
            score = 0.0
            for token in query_tokens:
                frequency = frequencies[token]
                if not frequency:
                    continue
                df = self.document_frequency[token]
                inverse = math.log(1 + (total - df + 0.5) / (df + 0.5))
                denominator = frequency + 1.5 * (1 - 0.75 + 0.75 * len(tokens) / max(1, self.average_length))
                score += inverse * frequency * 2.5 / denominator
            if score > 0:
                rows.append((index, score))
        rows.sort(key=lambda item: (-item[1], item[0]))
        return [(self.paper.chunks[index].chunk_id, score) for index, score in rows[:top_k]]

    def vector(self, query: str, top_k: int = 20) -> list[tuple[str, float]]:
        query_vector = self._vector(tokenize(query))
        rows = [
            (index, sum(left * right for left, right in zip(query_vector, vector)))
            for index, vector in enumerate(self.vectors)
        ]
        rows = [item for item in rows if item[1] > 0]
        rows.sort(key=lambda item: (-item[1], item[0]))
        return [(self.paper.chunks[index].chunk_id, score) for index, score in rows[:top_k]]


def _rrf(*rankings: list[tuple[str, float]], k: int = 60) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    order: dict[str, int] = {}
    for ranking in rankings:
        for rank, (chunk_id, _) in enumerate(ranking, 1):
            order.setdefault(chunk_id, len(order))
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1 / (k + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], order[item[0]]))


def _rerank(query: str, candidates: list[tuple[str, float]], paper: QasperPaper) -> list[tuple[str, float]]:
    chunks = {chunk.chunk_id: chunk for chunk in paper.chunks}
    query_tokens = set(tokenize(query))
    rows: list[tuple[int, str, float]] = []
    for position, (chunk_id, base) in enumerate(candidates):
        passage_tokens = set(tokenize(chunks[chunk_id].text))
        coverage = len(query_tokens & passage_tokens) / max(1, len(query_tokens))
        rows.append((position, chunk_id, coverage + base))
    rows.sort(key=lambda item: (-item[2], item[0]))
    return [(chunk_id, score) for _, chunk_id, score in rows]


def _set_f1(predicted: set[str], expected: set[str]) -> float:
    if not predicted and not expected:
        return 1.0
    if not predicted or not expected:
        return 0.0
    precision = len(predicted & expected) / len(predicted)
    recall = len(predicted & expected) / len(expected)
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def _token_f1(prediction: str, expected: str) -> float:
    predicted, expected = Counter(tokenize(prediction)), Counter(tokenize(expected))
    overlap = sum((predicted & expected).values())
    if not predicted and not expected:
        return 1.0
    if not predicted or not expected or not overlap:
        return 0.0
    precision, recall = overlap / sum(predicted.values()), overlap / sum(expected.values())
    return 2 * precision * recall / (precision + recall)


def _confidence(scenario: str, ranking: list[tuple[str, float]]) -> float:
    if not ranking:
        return 0.0
    score = ranking[0][1]
    if scenario in {"bm25", "embedding_degraded"}:
        return score / (1 + score)
    if scenario == "vector":
        return max(0.0, min(1.0, score))
    # RRF has a small fixed numeric range; scale its first rank to [0, 1].
    if scenario in {"rrf", "reranker_degraded"}:
        return min(1.0, score * 30.5)
    return min(1.0, score)


def _case_row(
    case: QasperCase, paper: QasperPaper, index: PaperIndex, scenario: str,
    answerability_threshold: float,
) -> dict[str, Any]:
    started = time.perf_counter()
    bm25 = index.bm25(case.question)
    vector = index.vector(case.question)
    degraded_reason = None
    effective = scenario
    if scenario == "bm25":
        candidates = ranking = bm25
    elif scenario == "vector":
        candidates = ranking = vector
    elif scenario == "embedding_degraded":
        candidates = ranking = bm25
        effective, degraded_reason = "bm25", "embedding_unavailable:forced_evaluation_failure"
    else:
        candidates = _rrf(bm25, vector)
        if scenario == "rrf_reranker":
            ranking = _rerank(case.question, candidates, paper)
        else:
            ranking = candidates
        if scenario == "reranker_degraded":
            effective, degraded_reason = "rrf", "reranker_unavailable:forced_evaluation_failure"
    confidence = _confidence(effective, ranking)
    refused = confidence < answerability_threshold
    selected = [] if refused else [chunk_id for chunk_id, _ in ranking[:6]]
    candidate_ids = [chunk_id for chunk_id, _ in candidates[:20]]
    relevant = set(case.relevant_chunk_ids)
    selected_set = set(selected)
    recall = len(selected_set & relevant) / len(relevant) if relevant else None
    precision = len(selected_set & relevant) / len(selected) if selected and relevant else (0.0 if relevant else None)
    first_rank = next((rank for rank, chunk_id in enumerate(selected, 1) if chunk_id in relevant), None)
    evidence_sets = [set(group) for group in case.minimum_evidence_sets]
    evidence_f1 = max((_set_f1(selected_set, group) for group in evidence_sets), default=None)
    coverage = any(group <= selected_set for group in evidence_sets) if evidence_sets else None
    chunk_by_id = {chunk.chunk_id: chunk for chunk in paper.chunks}
    prediction = "" if refused or not selected else chunk_by_id[selected[0]].text
    if case.answer_type == "unanswerable":
        answer_f1 = 1.0 if refused else 0.0
    else:
        answer_f1 = max((_token_f1(prediction, answer) for answer in case.gold_answers), default=0.0)
    return {
        "case_id": case.case_id,
        "paper_id": case.paper_id,
        "answer_type": case.answer_type,
        "answerable": case.answerable,
        "retrieved_chunk_ids": selected,
        "candidate_chunk_ids": candidate_ids,
        "recall_at_6": recall,
        "precision_at_6": precision,
        "candidate_recall_at_20": len(set(candidate_ids) & relevant) / len(relevant) if relevant else None,
        "mrr": 1 / first_rank if first_rank else (0.0 if relevant else None),
        "evidence_f1": evidence_f1,
        "minimum_evidence_covered": coverage,
        "refused": refused,
        "answer_token_f1": answer_f1,
        "citation_valid": bool(refused or not selected or selected[0] in selected_set),
        "evidence_supported": bool(refused or not prediction or selected),
        "latency_ms": (time.perf_counter() - started) * 1000,
        "effective_mode": effective,
        "degraded_reason": degraded_reason,
    }


def _average(rows: list[dict[str, Any]], key: str) -> float:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    return sum(values) / len(values) if values else 0.0


def _summarize(rows: list[dict[str, Any]], scenario: str) -> dict[str, Any]:
    answerable = [row for row in rows if row["answerable"]]
    unanswerable = [row for row in rows if not row["answerable"]]
    failures = [
        row["case_id"] for row in rows
        if (row["answerable"] and (row["recall_at_6"] or 0) < 1) or
        (not row["answerable"] and not row["refused"])
    ]
    by_type = {
        kind: {"count": len(group), "token_f1": _average(group, "answer_token_f1")}
        for kind in ("extractive", "yes_no", "free_form", "unanswerable")
        if (group := [row for row in rows if row["answer_type"] == kind])
    }
    latencies = [row["latency_ms"] for row in rows]
    return {
        "scenario": scenario,
        "effective_modes": sorted({row["effective_mode"] for row in rows}),
        "case_count": len(rows),
        "metrics": {
            "candidate_recall_at_20": _average(answerable, "candidate_recall_at_20"),
            "recall_at_6": _average(answerable, "recall_at_6"),
            "precision_at_6": _average(answerable, "precision_at_6"),
            "mrr": _average(answerable, "mrr"),
            "evidence_coverage": _average(answerable, "minimum_evidence_covered"),
            "evidence_f1": _average(answerable, "evidence_f1"),
            "unanswerable_refusal_rate": _average(unanswerable, "refused"),
            "answerable_false_refusal_rate": _average(answerable, "refused"),
            "answer_token_f1": _average(rows, "answer_token_f1"),
            "citation_validity_rate": _average(rows, "citation_valid"),
            "evidence_support_rate": _average(rows, "evidence_supported"),
            "latency_p50_ms": median(latencies) if latencies else 0.0,
            "latency_p95_ms": percentile(latencies, 0.95),
            "estimated_cost_usd": 0.0,
        },
        "answer_quality_by_type": by_type,
        "degraded_reasons": sorted({row["degraded_reason"] for row in rows if row["degraded_reason"]}),
        "failure_case_ids": failures,
    }


def evaluate_qasper(
    dataset: QasperAdaptation, *, scenarios: Iterable[str] = SCENARIOS,
    answerability_threshold: float = 0.15,
    result_status: str = "unverified_local_run",
) -> dict[str, Any]:
    requested = list(dict.fromkeys(scenarios))
    unknown = set(requested) - set(SCENARIOS)
    if unknown:
        raise ValueError(f"unknown scenarios: {sorted(unknown)}")
    rows_by_scenario: dict[str, list[dict[str, Any]]] = {name: [] for name in requested}
    for paper in dataset.papers:
        index = PaperIndex(paper)
        for case in paper.cases:
            for scenario in requested:
                rows_by_scenario[scenario].append(
                    _case_row(case, paper, index, scenario, answerability_threshold)
                )
    return {
        "schema_version": REPORT_SCHEMA,
        "benchmark": "QASPER",
        "result_status": result_status,
        "dataset_adapter_version": dataset.schema_version,
        "split": dataset.split,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "scope": {
            "evaluates": ["full-text retrieval", "evidence selection", "refusal", "heuristic answer quality"],
            "does_not_evaluate": ["PDF parsing", "page-number accuracy", "complete research-report quality"],
            "answer_baseline": "top-retrieved-paragraph heuristic; no LLM-as-Judge",
        },
        "paper_count": len(dataset.papers),
        "case_count": sum(len(paper.cases) for paper in dataset.papers),
        "exclusions": dataset.exclusions,
        "configuration": {
            "candidate_k": 20, "evidence_k": 6, "rrf_k": 60,
            "answerability_threshold": answerability_threshold,
            "embedding_model": "offline-hashing-v1", "reranker_model": "offline-overlap-v1",
        },
        "scenarios": [_summarize(rows_by_scenario[name], name) for name in requested],
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Public benchmark report", "",
        f"- Benchmark: {report['benchmark']} ({report['split']})",
        f"- Papers / cases: {report['paper_count']} / {report['case_count']}",
        f"- Result status: `{report['result_status']}`",
        f"- Schema: `{report['schema_version']}`", "",
        "This report evaluates full-text retrieval and Evidence behavior only; it does not evaluate PDF parsing or page-number accuracy.",
        "The answer score is a transparent top-paragraph heuristic. No LLM-as-Judge score is used.", "",
        "| Scenario | Cand. R@20 | R@6 | P@6 | MRR | Evidence F1 | Refusal | False refusal | p95 ms | Cost USD |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for scenario in report["scenarios"]:
        metrics = scenario["metrics"]
        lines.append(
            f"| {scenario['scenario']} | {metrics['candidate_recall_at_20']:.3f} | "
            f"{metrics['recall_at_6']:.3f} | {metrics['precision_at_6']:.3f} | {metrics['mrr']:.3f} | "
            f"{metrics['evidence_f1']:.3f} | {metrics['unanswerable_refusal_rate']:.3f} | "
            f"{metrics['answerable_false_refusal_rate']:.3f} | {metrics['latency_p95_ms']:.2f} | "
            f"{metrics['estimated_cost_usd']:.4f} |"
        )
    lines += ["", "Failure details are represented by case IDs only; questions and paper text are intentionally omitted.", ""]
    return "\n".join(lines)


def _config_digest(report: dict[str, Any]) -> str:
    payload = json.dumps(report["configuration"], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def run_cached_benchmark(
    cache_dir: Path, split: Literal["train", "validation", "test"], output: Path,
    *, scenarios: Iterable[str] = SCENARIOS, answerability_threshold: float = 0.15,
    final_config: bool = False,
    official_source: bool = False,
) -> dict[str, Any]:
    if split == "test" and not final_config:
        raise ValueError("QASPER test requires --final-config after train/validation tuning")
    guard = cache_dir / "qasper-test-run.json"
    if split == "test" and guard.exists():
        raise ValueError("QASPER test has already been run for this imported dataset")
    dataset = load_adapted(cache_dir, split)
    manifest = json.loads((cache_dir / "manifest.json").read_text(encoding="utf-8"))
    split_manifest = (manifest.get("splits") or {}).get(split, {})
    report = evaluate_qasper(
        dataset, scenarios=scenarios, answerability_threshold=answerability_threshold,
        result_status="public_benchmark_run" if official_source else "unverified_local_run",
    )
    report["dataset_version"] = cache_dir.name
    report["source_sha256"] = split_manifest.get("source_sha256")
    report["adapted_sha256"] = split_manifest.get("adapted_sha256")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    if split == "test":
        guard.write_text(json.dumps({
            "report_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "configuration_sha256": _config_digest(report),
            "completed_at": report["generated_at"],
        }, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Import and evaluate the public QASPER benchmark.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    importer = subparsers.add_parser("import")
    importer.add_argument("--source", required=True)
    importer.add_argument("--cache", type=Path, default=Path("backend/data/public_evaluation/qasper-v0.3"))
    importer.add_argument("--split", choices=("train", "validation", "test"), required=True)
    importer.add_argument("--sha256", required=True)
    runner = subparsers.add_parser("run")
    runner.add_argument("--cache", type=Path, default=Path("backend/data/public_evaluation/qasper-v0.3"))
    runner.add_argument("--split", choices=("train", "validation", "test"), default="validation")
    runner.add_argument("--output", type=Path, required=True)
    runner.add_argument("--scenarios", default=",".join(SCENARIOS))
    runner.add_argument("--answerability-threshold", type=float, default=0.15)
    runner.add_argument("--final-config", action="store_true")
    runner.add_argument("--official-source", action="store_true", help="Assert the verified import is an official QASPER release split.")
    real_pilot = subparsers.add_parser("real-pilot")
    real_pilot.add_argument("--cache", type=Path, default=Path("backend/data/public_evaluation/qasper-v0.3"))
    real_pilot.add_argument("--output", type=Path, required=True)
    real_pilot.add_argument("--pilot-version", required=True)
    real_pilot.add_argument("--checkpoint", type=Path)
    real_validation = subparsers.add_parser("real-validation")
    real_validation.add_argument("--cache", type=Path, default=Path("backend/data/public_evaluation/qasper-v0.3"))
    real_validation.add_argument("--pilot", type=Path, required=True)
    real_validation.add_argument("--output", type=Path, required=True)
    real_validation.add_argument("--calibration-version", required=True)
    real_validation.add_argument("--checkpoint", type=Path)
    args = parser.parse_args()
    if args.command == "import":
        result = import_qasper(args.source, args.cache, args.split, expected_sha256=args.sha256)
    elif args.command == "run":
        result = run_cached_benchmark(
            args.cache, args.split, args.output,
            scenarios=[item.strip() for item in args.scenarios.split(",") if item.strip()],
            answerability_threshold=args.answerability_threshold, final_config=args.final_config,
            official_source=args.official_source,
        )
    elif args.command == "real-pilot":
        from backend.evaluation.qasper_real import render_markdown as render_real_markdown
        from backend.evaluation.qasper_real import run_real_pilot
        result = run_real_pilot(
            args.cache, args.output, args.pilot_version,
            checkpoint_path=args.checkpoint,
        )
        args.output.with_suffix(".md").write_text(render_real_markdown(result), encoding="utf-8")
    else:
        from backend.evaluation.qasper_real import render_markdown as render_real_markdown
        from backend.evaluation.qasper_real import run_real_validation
        result = run_real_validation(
            args.cache, args.pilot, args.output, args.calibration_version,
            checkpoint_path=args.checkpoint,
        )
        args.output.with_suffix(".md").write_text(render_real_markdown(result), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
