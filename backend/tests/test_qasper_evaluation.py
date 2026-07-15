import hashlib
import json
from pathlib import Path

import pytest

from backend.evaluation.qasper import adapt_qasper, import_qasper, load_adapted
from backend.evaluation.qasper_benchmark import evaluate_qasper, render_markdown, run_cached_benchmark


FIXTURE = Path("backend/evaluation/fixtures/qasper_format_sample.json")


def fixture_raw():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_qasper_adapter_has_stable_chunks_and_strict_filtering():
    adapted = adapt_qasper(fixture_raw(), "validation")
    assert len(adapted.papers) == 1
    paper = adapted.papers[0]
    assert [chunk.chunk_id for chunk in paper.chunks] == [
        "qasper:paper-alpha:s0:p0", "qasper:paper-alpha:s0:p1",
        "qasper:paper-alpha:s1:p0", "qasper:paper-alpha:s1:p1",
    ]
    cases = {case.case_id: case for case in paper.cases}
    assert set(cases) == {"q-answerable", "q-unanswerable", "q-resolved-duplicate"}
    assert cases["q-answerable"].minimum_evidence_sets == [
        ["qasper:paper-alpha:s0:p0"], ["qasper:paper-alpha:s0:p0"]
    ]
    assert cases["q-resolved-duplicate"].minimum_evidence_sets == [[
        "qasper:paper-alpha:s0:p1", "qasper:paper-alpha:s1:p0"
    ]]
    assert adapted.exclusions == {
        "ambiguous_duplicate_evidence": 1,
        "answerability_disagreement": 1,
        "figure_or_table": 1,
    }


def test_qasper_import_verifies_sha_and_cache_digest(tmp_path):
    digest = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    summary = import_qasper(str(FIXTURE), tmp_path, "validation", expected_sha256=digest)
    assert summary["case_count"] == 3
    assert load_adapted(tmp_path, "validation").split == "validation"
    (tmp_path / "adapted-validation.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="version/SHA"):
        load_adapted(tmp_path, "validation")


def test_qasper_import_rejects_wrong_source_digest(tmp_path):
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        import_qasper(str(FIXTURE), tmp_path, "train", expected_sha256="0" * 64)


def test_public_report_contains_required_metrics_without_text():
    report = evaluate_qasper(adapt_qasper(fixture_raw(), "validation"))
    assert report["schema_version"] == "public-paper-benchmark-v1"
    assert [item["scenario"] for item in report["scenarios"]] == [
        "bm25", "vector", "rrf", "rrf_reranker", "embedding_degraded", "reranker_degraded"
    ]
    metrics = report["scenarios"][0]["metrics"]
    for name in (
        "candidate_recall_at_20", "recall_at_6", "precision_at_6", "mrr",
        "evidence_coverage", "evidence_f1", "unanswerable_refusal_rate",
        "answerable_false_refusal_rate", "latency_p50_ms", "latency_p95_ms",
        "citation_validity_rate", "evidence_support_rate", "estimated_cost_usd",
    ):
        assert name in metrics
    serialized = json.dumps(report)
    assert "What does the method combine?" not in serialized
    assert "The method combines lexical" not in serialized
    assert "Failure details" in render_markdown(report)


def test_test_split_requires_final_config_and_is_single_use(tmp_path):
    digest = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    import_qasper(str(FIXTURE), tmp_path, "test", expected_sha256=digest)
    with pytest.raises(ValueError, match="final-config"):
        run_cached_benchmark(tmp_path, "test", tmp_path / "report.json")
    run_cached_benchmark(tmp_path, "test", tmp_path / "report.json", final_config=True)
    with pytest.raises(ValueError, match="already been run"):
        run_cached_benchmark(tmp_path, "test", tmp_path / "report-2.json", final_config=True)
