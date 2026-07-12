import json

import pytest

from backend.ask_retrieval import AskPaperRetrievalService
from backend.evaluation.ask_paper import (
    CaseObservation,
    calculate_metrics,
    compare_reports,
    evaluate,
    quality_gate,
)


def observation(**overrides):
    values = {
        "case_id": "case",
        "retrieved_chunk_ids": ["right", "noise"],
        "relevant_chunk_ids": ["right"],
        "allowed_evidence": ["right"],
        "requested_section": "Methods",
        "retrieved_sections": ["Methods", "Methods"],
        "should_refuse": False,
        "refused": False,
        "latency_ms": 10.0,
    }
    values.update(overrides)
    return CaseObservation(**values)


def test_metric_calculation_covers_multiple_relevant_chunks_and_percentiles():
    rows = [
        observation(
            retrieved_chunk_ids=["noise", "a", "b"],
            relevant_chunk_ids=["a", "b"],
            allowed_evidence=["a", "b"],
            retrieved_sections=["Methods", "Methods", "Methods"],
            latency_ms=10,
        ),
        observation(
            case_id="refuse",
            retrieved_chunk_ids=[],
            relevant_chunk_ids=[],
            allowed_evidence=[],
            retrieved_sections=[],
            should_refuse=True,
            refused=True,
            latency_ms=20,
        ),
    ]

    metrics = calculate_metrics(rows)

    assert metrics["recall_at_6"] == 1.0
    assert metrics["mrr"] == 0.5
    assert metrics["evidence_coverage"] == 1.0
    assert metrics["noise_rate"] == pytest.approx(1 / 3)
    assert metrics["no_answer_refusal_rate"] == 1.0
    assert metrics["illegal_citation_retention_rate"] == 0.0
    assert metrics["latency_p50_ms"] == 15
    assert metrics["latency_p95_ms"] == pytest.approx(19.5)


def test_metrics_handle_empty_dataset_and_detect_section_boundary():
    assert all(value == 0 for value in calculate_metrics([]).values())
    metrics = calculate_metrics([observation(retrieved_sections=["Methods", "Results"])])
    assert metrics["section_boundary_rate"] == 0.5


def test_bm25_ties_are_stable_by_chunk_position():
    index = AskPaperRetrievalService._build_lexical([
        {"chunk_id": "first", "text": "same"},
        {"chunk_id": "second", "text": "same"},
    ])
    assert AskPaperRetrievalService.bm25(index, "same", 6)[0][0] == 0


def test_fixed_offline_baselines_pass_quality_gate_and_filter_sections():
    for mode in ("bm25", "filtered-hybrid"):
        report = evaluate(mode=mode)
        assert report["baseline_eligible"] is True
        assert report["effective_mode"] == mode
        assert quality_gate(report) == []
        assert all(
            set(case["retrieved_sections"]) <= {case["requested_section"]}
            for case in report["cases"]
            if case["requested_section"]
        )


def test_filtered_hybrid_beats_raw_hybrid_noise_without_recall_loss():
    reports = [evaluate(mode=mode) for mode in ("bm25", "hybrid", "filtered-hybrid")]
    comparison = compare_reports(reports)
    assert comparison["acceptance_failures"] == []
    assert comparison["filtered_minus_hybrid"]["recall_at_6"] >= 0
    assert comparison["filtered_minus_hybrid"]["noise_rate"] < 0
    assert comparison["filtered_minus_bm25"]["noise_rate"] <= 0
    filtered = reports[2]
    assert filtered["candidate_totals"]["vector_removed"] > 0
    assert all("vector_candidates_raw" in case for case in filtered["cases"])


def test_embedding_degradation_is_not_labeled_as_hybrid_baseline():
    report = evaluate(mode="degraded")
    assert report["requested_mode"] == "degraded"
    assert report["effective_mode"] == "bm25"
    assert report["baseline_eligible"] is False
    assert report["degraded_reasons"] == ["embedding_unavailable:RuntimeError"]


def test_evaluate_accepts_empty_dataset(tmp_path):
    dataset = tmp_path / "empty.json"
    dataset.write_text(json.dumps({"version": "empty", "chunks": [], "cases": []}), encoding="utf-8")
    report = evaluate(dataset, "bm25")
    assert report["case_count"] == 0
    assert report["metrics"]["recall_at_6"] == 0.0
