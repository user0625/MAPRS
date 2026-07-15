import hashlib
import json
import math
from collections import Counter
from pathlib import Path

import pytest

from backend.core.config import AppSettings
from backend.evaluation.qasper import (
    adapt_qasper,
    import_qasper,
    load_adapted,
    load_qasper_state_index,
)
from backend.evaluation.qasper_real import (
    CHECKPOINT_SCHEMA,
    REAL_REPORT_SCHEMA,
    _canonical_sha,
    _dataset_signature,
    _configuration_fingerprint,
    _rrf_candidates,
    collect_real_rows,
    run_real_pilot,
    run_real_validation,
    select_pilot_cases,
)
from backend.reranker import BaseReranker
from backend.tools.embedder import BaseEmbedder


FIXTURE = Path("backend/evaluation/fixtures/qasper_format_sample.json")
OFFICIAL_TRAIN = Path("qasper/qasper-train-v0.3.json")


class CountingEmbedder(BaseEmbedder):
    model_name = "text-embedding-v4-fixture"

    def __init__(self):
        self.request_count = 0

    @staticmethod
    def _vector(text: str) -> list[float]:
        lowered = text.casefold()
        return [
            float("combine" in lowered or "method" in lowered),
            float("duplicate" in lowered or "where" in lowered),
            float("evaluation" in lowered or "precision" in lowered),
            0.1,
        ]

    def embed_text(self, text: str) -> list[float]:
        self.request_count += 1
        return self._vector(text)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.request_count += 1
        return [self._vector(text) for text in texts]


class CountingReranker(BaseReranker):
    model_name = "qwen3-rerank-fixture"

    def __init__(self):
        self.request_count = 0

    def score(self, query: str, passages: list[str], timeout: float) -> list[float]:
        self.request_count += 1
        lowered = query.casefold()
        if "gpu" in lowered:
            return [0.1] * len(passages)
        if "repeated" in lowered:
            return [0.95 if "paragraph is duplicated" in passage.casefold() else 0.1 for passage in passages]
        return [0.95 if "combines lexical" in passage.casefold() else 0.1 for passage in passages]


class FlatReranker(CountingReranker):
    def score(self, query: str, passages: list[str], timeout: float) -> list[float]:
        self.request_count += 1
        return [0.5] * len(passages)


def _import(cache: Path, raw: dict, split: str) -> None:
    source = cache.parent / f"{split}.json"
    source.write_text(json.dumps(raw), encoding="utf-8")
    import_qasper(
        str(source), cache, split, expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
    )


def _settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        _env_file=None,
        project_root=tmp_path,
        embedding_provider="openai_compatible",
        embedding_model="text-embedding-v4-fixture",
        embedding_api_key="fixture",
        ask_reranker_model="qwen3-rerank-fixture",
        ask_reranker_api_key="fixture",
        ask_candidate_count=20,
        ask_evidence_count=2,
        ask_reranker_mode="shadow",
        ask_reranker_timeout=10,
        ask_index_dir=Path("indexes"),
    )


def test_qasper_state_bridge_is_stable_content_addressed_and_has_no_pages(tmp_path):
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    cache = tmp_path / "cache"
    _import(cache, raw, "validation")
    first = load_qasper_state_index(cache, "validation")
    state_path = cache / first["paper-alpha"]["path"]
    payload = state_path.read_text(encoding="utf-8")
    assert first["paper-alpha"]["state_sha256"] in state_path.name
    assert "page_start" not in payload and "page_end" not in payload

    other = tmp_path / "other-cache"
    _import(other, raw, "validation")
    assert load_qasper_state_index(other, "validation") == first

    state = json.loads(payload)
    state["document"]["chunks"][0]["text"] += " changed"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ValueError, match="state version/SHA"):
        load_adapted(cache, "validation")


def test_qasper_import_rejects_paper_level_split_leakage(tmp_path):
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    cache = tmp_path / "cache"
    _import(cache, raw, "train")
    with pytest.raises(ValueError, match="paper-level split leakage"):
        _import(cache, raw, "validation")


def test_official_train_pilot_quota_is_exact_and_paper_capped():
    dataset = adapt_qasper(json.loads(OFFICIAL_TRAIN.read_text(encoding="utf-8")), "train")
    assert len(dataset.papers) == 840
    cases = select_pilot_cases(dataset)
    assert Counter(case.answer_type for case in cases) == {
        "extractive": 45, "free_form": 20, "yes_no": 10, "unanswerable": 25,
    }
    by_paper = Counter(case.paper_id for case in cases)
    assert len(by_paper) == 26
    assert max(by_paper.values()) <= 4
    chunks = {paper.paper_id: len(paper.chunks) for paper in dataset.papers}
    estimated_batches = len(cases) + sum(math.ceil(chunks[paper_id] / 8) for paper_id in by_paper)
    assert estimated_batches <= 400
    assert select_pilot_cases(dataset) == cases


def test_real_collection_reuses_production_ranking_and_checkpoint_without_recalling(tmp_path):
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    cache = tmp_path / "cache"
    _import(cache, raw, "validation")
    dataset = load_adapted(cache, "validation")
    cases = [case for paper in dataset.papers for case in paper.cases]
    embedder, reranker = CountingEmbedder(), CountingReranker()
    checkpoint = tmp_path / "checkpoint.json"
    rows, requests = collect_real_rows(
        cache, dataset, cases, _settings(tmp_path), checkpoint,
        embedder=embedder, reranker=reranker,
    )
    assert len(rows) == 3
    assert reranker.request_count == requests["rerank_requests"] == 3
    for row in rows:
        production_order = [item["chunk_id"] for item in row.candidate_scores]
        assert _rrf_candidates(
            row, candidate_count=20, bm25_min_score=0,
            vector_min_similarity=-1, rrf_k=60,
        ) == production_order

    before = (embedder.request_count, reranker.request_count)
    resumed, resumed_requests = collect_real_rows(
        cache, dataset, cases, _settings(tmp_path), checkpoint,
        embedder=embedder, reranker=reranker,
    )
    assert len(resumed) == 3
    assert resumed_requests == requests
    assert (embedder.request_count, reranker.request_count) == before
    serialized = checkpoint.read_text(encoding="utf-8")
    for case in cases:
        assert case.question not in serialized
    assert "api_key" not in serialized.casefold()
    assert "authorization" not in serialized.casefold()
    payload = json.loads(serialized)
    assert payload["schema_version"] == CHECKPOINT_SCHEMA
    assert all(row["wall_latency_ms"] >= row["query_latency_ms"] for row in payload["completed"])
    assert payload["completed"][0]["index_memory_cache_hit"] is False
    assert any(row["index_memory_cache_hit"] for row in payload["completed"][1:])
    assert all(not (row["index_build_ms"] and row["index_load_ms"]) for row in payload["completed"])


def test_checkpoint_rejects_schema_model_sample_and_configuration_mismatches(tmp_path):
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    cache = tmp_path / "cache"
    _import(cache, raw, "validation")
    dataset = load_adapted(cache, "validation")
    cases = [case for paper in dataset.papers for case in paper.cases]
    checkpoint = tmp_path / "checkpoint.json"
    collect_real_rows(
        cache, dataset, cases, _settings(tmp_path), checkpoint,
        embedder=CountingEmbedder(), reranker=CountingReranker(),
    )
    original = json.loads(checkpoint.read_text(encoding="utf-8"))

    checkpoint.write_text(json.dumps({**original, "schema_version": "qasper-real-checkpoint-v1"}))
    with pytest.raises(ValueError, match="schema mismatch.*audit-only"):
        collect_real_rows(cache, dataset, cases, _settings(tmp_path), checkpoint)

    checkpoint.write_text(json.dumps(original))
    changed_model = _settings(tmp_path).model_copy(update={"embedding_model": "other-model"})
    with pytest.raises(ValueError, match="model mismatch"):
        collect_real_rows(cache, dataset, cases, changed_model, checkpoint)

    with pytest.raises(ValueError, match="sample selection mismatch"):
        collect_real_rows(cache, dataset, cases[:-1], _settings(tmp_path), checkpoint)

    changed_config = _settings(tmp_path).model_copy(update={"ask_candidate_count": 30})
    with pytest.raises(ValueError, match="configuration mismatch"):
        collect_real_rows(cache, dataset, cases, changed_config, checkpoint)

    checkpoint.write_text(json.dumps({**original, "dataset_sha256": "0" * 64}))
    with pytest.raises(ValueError, match="dataset mismatch"):
        collect_real_rows(cache, dataset, cases, _settings(tmp_path), checkpoint)


def test_real_collection_stops_when_request_limit_is_exceeded(tmp_path):
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    cache = tmp_path / "cache"
    _import(cache, raw, "validation")
    dataset = load_adapted(cache, "validation")
    cases = [case for paper in dataset.papers for case in paper.cases]
    with pytest.raises(RuntimeError, match="rerank request limit exceeded"):
        collect_real_rows(
            cache, dataset, cases, _settings(tmp_path), tmp_path / "limited.json",
            embedder=CountingEmbedder(), reranker=CountingReranker(), rerank_limit=1,
        )


def test_real_pilot_collects_once_and_uses_only_offline_grid_replay(tmp_path):
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    cache = tmp_path / "cache"
    _import(cache, raw, "train")
    embedder, reranker = CountingEmbedder(), CountingReranker()
    output = tmp_path / "pilot.json"
    report = run_real_pilot(
        cache, output, "fixture-pilot", settings=_settings(tmp_path),
        embedder=embedder, reranker=reranker, require_preflight=False,
        quotas={"extractive": 1, "free_form": 1, "yes_no": 0, "unanswerable": 1},
    )
    assert report["case_count"] == 3
    assert report["selection_grid"]["upstream_collections"] == 1
    assert report["selection_grid"]["offline_replay_only"] is True
    assert reranker.request_count == report["request_counts"]["rerank_requests"] == 3
    assert report["quality_gate"]["passed"] is True, report["quality_gate"]
    assert report["validation_authorized"] is True
    assert report["validation_scope"] == "retrieval_only"
    assert report["dataset_paper_count"] == 1
    assert report["evaluated_paper_count"] == 1
    assert set(report["quality_gates"]) == {"retrieval", "reranker", "refusal"}
    assert report["production_recommendation"] == {
        "embedding": "candidate_for_validation", "reranker": "disabled",
    }
    assert set(report["reranker_diagnostics"]) >= {
        "feasible_configuration_count", "recall_protection", "precision_first",
    }
    assert all(item["metrics"]["latency_p50_ms"] > 0 for item in report["scenarios"])
    serialized = output.read_text(encoding="utf-8")
    assert "What does the method combine?" not in serialized
    assert "The method combines lexical" not in serialized


def test_retrieval_gate_can_authorize_validation_when_reranker_and_refusal_fail(tmp_path):
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    cache = tmp_path / "cache"
    _import(cache, raw, "train")
    report = run_real_pilot(
        cache, tmp_path / "pilot-flat.json", "fixture-flat",
        settings=_settings(tmp_path), embedder=CountingEmbedder(), reranker=FlatReranker(),
        require_preflight=False,
        quotas={"extractive": 1, "free_form": 1, "yes_no": 0, "unanswerable": 1},
    )
    assert report["quality_gate"]["passed"] is False
    assert report["quality_gates"]["retrieval"]["passed"] is True
    assert report["quality_gates"]["reranker"]["passed"] is False
    assert report["quality_gates"]["refusal"]["passed"] is False
    assert report["validation_authorized"] is True
    assert report["validation_scope"] == "retrieval_only"
    assert report["reranker_diagnostics"]["feasible_configuration_count"] == 0
    assert report["reranker_configuration"] is None
    assert report["production_recommendation"] == {
        "embedding": "candidate_for_validation", "reranker": "disabled",
    }


def test_real_validation_requires_matching_successful_pilot_and_replays_fixed_thresholds(tmp_path):
    validation_raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    train_raw = {"paper-beta": next(iter(validation_raw.values()))}
    cache = tmp_path / "cache"
    _import(cache, train_raw, "train")
    _import(cache, validation_raw, "validation")
    settings = _settings(tmp_path)
    selected = {
        "candidate_count": 20,
        "evidence_count": 2,
        "bm25_min_score": 0.0,
        "vector_min_similarity": -1.0,
        "rrf_k": 60,
    }
    configured = settings.model_copy(update={"ask_reranker_mode": "disabled"})
    pilot = {
        "schema_version": REAL_REPORT_SCHEMA,
        "run_level": "pilot",
        "run_version": "pilot-fixture",
        "split": "train",
        "quality_gate": {"passed": False, "failures": ["reranker failed"]},
        "quality_gates": {
            "retrieval": {"name": "retrieval", "passed": True, "failures": []},
            "reranker": {"name": "reranker", "passed": False, "failures": ["reranker failed"]},
            "refusal": {"name": "refusal", "passed": False, "failures": ["refusal failed"]},
        },
        "validation_authorized": True,
        "validation_scope": "retrieval_only",
        "data_signature": _dataset_signature(cache),
        "configuration": selected,
        "hybrid_configuration": selected,
        "configuration_sha256": _canonical_sha(_configuration_fingerprint(configured, selected)),
        "bm25_configuration": {
            "candidate_count": 20, "evidence_count": 2, "bm25_min_score": 0.0,
            "vector_min_similarity": None, "rrf_k": 60,
        },
    }
    pilot_path = tmp_path / "pilot.json"
    pilot_path.write_text(json.dumps(pilot), encoding="utf-8")
    output = tmp_path / "validation-report.json"
    validation_reranker = CountingReranker()
    report = run_real_validation(
        cache, pilot_path, output, "calibration-fixture",
        settings=settings, embedder=CountingEmbedder(), reranker=validation_reranker,
        require_preflight=False,
    )
    assert report["case_count"] == 3
    assert report["thresholds_replayed_without_tuning"] is True
    assert report["quality_gate"]["passed"] is False
    assert {item.split("=", 1)[0] for item in report["quality_gate"]["failures"]} == {
        "recall_at_6", "mrr", "evidence_coverage",
    }
    assert report["production_recommendation"] == {
        "embedding": "keep_current", "reranker": "disabled",
    }
    assert report["request_counts"]["rerank_requests"] == 0
    assert validation_reranker.request_count == 0
    assert [item["scenario"] for item in report["scenarios"]] == [
        "bm25-offline-baseline", "real-hybrid",
    ]
    assert not (cache / "real-calibration-versions.json").exists()
    serialized = output.read_text(encoding="utf-8")
    for paper in load_adapted(cache, "validation").papers:
        for case in paper.cases:
            assert case.question not in serialized

    pilot["data_signature"]["manifest_sha256"] = "0" * 64
    bad = tmp_path / "bad-pilot.json"
    bad.write_text(json.dumps(pilot), encoding="utf-8")
    with pytest.raises(ValueError, match="dataset SHA"):
        run_real_validation(
            cache, bad, tmp_path / "should-not-run.json", "bad",
            settings=settings, require_preflight=False,
        )
