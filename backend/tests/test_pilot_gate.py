import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.core.config import AppSettings
from backend.evaluation.pilot import derive_pilot_view, run_pilot_gate
from backend.evaluation.real_dataset import Split, load_dataset


TARGET = Path("backend/data/private_evaluation/target-v1")
ADJUDICATION = TARGET / "expert_adjudication.jsonl"


def test_pilot_view_is_read_only_and_never_allows_test():
    cases_path = TARGET / "cases.jsonl"
    before = hashlib.sha256(cases_path.read_bytes()).hexdigest()
    view = derive_pilot_view(TARGET, ADJUDICATION)
    assert len(view) == 20
    assert {case.split for case in view} == {Split.VALIDATION}
    assert all(case.review_status.value == "reviewed" for case in view)
    assert hashlib.sha256(cases_path.read_bytes()).hexdigest() == before
    with pytest.raises(ValueError, match="test split"):
        derive_pilot_view(TARGET, ADJUDICATION, split=Split.TEST)


def test_pilot_report_is_non_production_and_redacted(tmp_path):
    _, _, all_cases = load_dataset(TARGET)
    by_question = {case.question: case for case in all_cases}
    calls = {"count": 0}

    class Service:
        def retrieve(self, paper_id, state_path, question, section):
            calls["count"] += 1
            case = by_question[question]
            ids = case.relevant_chunk_ids[:6]
            if not ids:
                state = json.loads(Path(state_path).read_text(encoding="utf-8"))
                ids = [state["document"]["chunks"][0]["chunk_id"]]
            scores = [{"chunk_id": item, "reranker_score": 0.9} for item in ids]
            diagnostics = SimpleNamespace(
                candidate_scores=scores,
                reranker_latency_ms=0.1,
                degraded_reason=None,
                bm25_candidates=len(ids),
                vector_candidates_raw=len(ids),
                vector_candidates_filtered=len(ids),
                vector_candidates_removed=0,
                rrf_candidates=len(ids),
            )
            return SimpleNamespace(hits=[(1.0, {"chunk_id": item}) for item in ids], diagnostics=diagnostics)

    output = tmp_path / "pilot.json"
    artifact = run_pilot_gate(
        TARGET,
        ADJUDICATION,
        output,
        "test-pilot",
        settings=AppSettings(_env_file=None, ask_reranker_model="fixture"),
        candidate_counts=(20, 30),
        vector_thresholds=(0, 0.2),
        evidence_thresholds=(0,),
        answerability_thresholds=(0.5,),
        bm25_k1_values=(1.2, 1.5),
        bm25_b_values=(0.5, 0.75),
        rrf_k_values=(40, 60),
        service_factory=lambda settings: Service(),
    )
    payload = output.read_text(encoding="utf-8")
    assert artifact["evidence_grade"] == "pilot_only"
    assert artifact["splits_used"] == ["validation"]
    assert artifact["test_accessed"] is False
    assert artifact["reranker_mode"] == "disabled"
    assert artifact["production_enablement_recommendation"] is None
    assert "cases" not in artifact["selected_validation_report"]
    assert artifact["candidate_collection_count"] == 1
    assert artifact["reranker_shadow_count"] == 1
    assert calls["count"] == 40  # 20 cases collected once + one selected shadow
    assert all(case.question not in payload for case in all_cases)
