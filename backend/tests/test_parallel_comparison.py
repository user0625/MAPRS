import pytest

from backend.evaluation.parallel_comparison import AnalysisModeRun, compare_modes


def run(mode, **updates):
    values = dict(
        mode=mode, dataset_version="reviewed-demo-v1", frozen_test_sha256="a" * 64,
        configuration_sha256="b" * 64, case_count=16, latency_p50_ms=100,
        latency_p95_ms=200, total_input_tokens=1000, total_output_tokens=500,
        estimated_cost_usd=0.1, evidence_recall=0.95, report_quality=0.9,
        coverage_gap_rate=0,
    )
    values.update(updates)
    return AnalysisModeRun(**values)


def test_parallel_is_recommended_only_when_quality_does_not_drop():
    report = compare_modes(run("serial"), run("parallel", latency_p95_ms=100))
    assert report["deltas"]["p95_speedup"] == 2
    assert report["default_mode_recommendation"] == "parallel"
    degraded = compare_modes(run("serial"), run("parallel", evidence_recall=0.94))
    assert degraded["quality_not_degraded"] is False
    assert degraded["default_mode_recommendation"] == "serial"


def test_comparison_rejects_dataset_or_config_mismatch():
    with pytest.raises(ValueError, match="dataset_version"):
        compare_modes(run("serial"), run("parallel", dataset_version="other"))
