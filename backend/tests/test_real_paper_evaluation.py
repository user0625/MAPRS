import json
import shutil
from pathlib import Path

import pytest

from backend.ask_retrieval import AskPaperRetrievalService
from backend.core.config import AppSettings
from backend.evaluation.calibrate import (
    _observations,
    RawCase,
    calibrate,
    collect_raw,
    run_frozen_test,
)
from backend.evaluation.generate_candidates import CandidateBatch, CandidateQuestion, generate_dataset
from backend.evaluation.real_dataset import (
    CASES_FILE,
    MANIFEST_FILE,
    PAPERS_FILE,
    DatasetManifest,
    DatasetValidationError,
    DistractorType,
    EvaluationCase,
    PaperRecord,
    ReviewStatus,
    Split,
    ValidationPolicy,
    freeze_test_split,
    load_dataset,
    validate_dataset,
    write_jsonl,
)
from backend.reranker import BaseReranker
from backend.tools.embedder import BaseEmbedder


FIXTURE = Path("backend/evaluation/fixtures/private_eval_sample")


class OneDimensionalEmbedder(BaseEmbedder):
    model_name = "fixture-embedding"

    def embed_text(self, text):
        return [1.0]


class FixtureReranker(BaseReranker):
    model_name = "fixture-reranker"

    def score(self, query, passages, timeout):
        return [0.95 if "combines" in text else 0.7 if "reports" in text else 0.1 for text in passages]


class CandidateClient:
    def generate_pydantic(self, *args, **kwargs):
        return CandidateBatch(candidates=[CandidateQuestion(
            question="What does the method combine?",
            language="en",
            section="Methods",
            answerable=True,
            relevant_chunk_ids=["c-method"],
            minimum_evidence_sets=[["c-method"]],
            distractor_type=DistractorType.SYNONYM,
        )])


def settings(**updates):
    values = {
        "project_root": ".",
        "embedding_provider": "openai_compatible",
        "embedding_model": "fixture-embedding",
        "embedding_api_key": "fixture",
        "ask_reranker_model": "fixture-reranker",
        "ask_reranker_api_key": "fixture",
        "ask_evidence_count": 6,
    }
    values.update(updates)
    return AppSettings(_env_file=None, **values)


def factory(config):
    return AskPaperRetrievalService(
        config, embedder=OneDimensionalEmbedder(), reranker=FixtureReranker()
    )


def copied_fixture(tmp_path):
    target = tmp_path / "private"
    shutil.copytree(FIXTURE, target)
    return target


def reviewed_demo_fixture(tmp_path):
    target = tmp_path / "reviewed-demo"
    target.mkdir()
    manifest = DatasetManifest(
        dataset_version="reviewed-demo-v1",
        created_at="2026-07-15T00:00:00Z",
        split_seed="reviewed-demo-seed",
        chunking_config={"chunk_size": 1200, "chunk_overlap": 150},
        dataset_label="human-reviewed demonstration set",
        reviewer_count=1,
        review_claim="Reviewed once by the project author.",
    )
    (target / MANIFEST_FILE).write_text(
        manifest.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    splits = [Split.ANALYSIS] * 6 + [Split.VALIDATION] * 2 + [Split.TEST] * 2
    papers = [
        PaperRecord(
            paper_id=f"paper-{paper_index}", state_path="unused.json",
            split=split, state_sha256="a" * 64, chunk_ids=["c1", "c2"],
        )
        for paper_index, split in enumerate(splits)
    ]
    distractors = list(ValidationPolicy.reviewed_demo().required_distractor_types)
    cases = []
    for paper_index, paper in enumerate(papers):
        for case_index in range(8):
            answerable = case_index < 6
            cases.append(EvaluationCase(
                id=f"case-{paper_index}-{case_index}", paper_id=paper.paper_id,
                split=paper.split, language="zh" if case_index < 4 else "en",
                question=f"Reviewed question {paper_index}-{case_index}?",
                answerable=answerable,
                relevant_chunk_ids=["c1", "c2"] if answerable else [],
                minimum_evidence_sets=[["c1", "c2"]] if answerable else [],
                distractor_type=distractors[(paper_index * 8 + case_index) % len(distractors)],
                review_status=ReviewStatus.REVIEWED,
                reviewer_notes="Single-reviewer demonstration fixture.",
            ))
    write_jsonl(target / PAPERS_FILE, papers)
    write_jsonl(target / CASES_FILE, cases)
    return target


def test_fixture_schema_and_paper_level_splits_validate():
    summary = validate_dataset(FIXTURE, ValidationPolicy.fixture())
    assert summary["paper_count"] == 5
    assert summary["reviewed_case_count"] == 5
    _, papers, cases = load_dataset(FIXTURE)
    split_by_paper = {paper.paper_id: paper.split for paper in papers}
    assert all(case.split == split_by_paper[case.paper_id] for case in cases)


def test_reviewed_demo_profile_enforces_reviewed_bilingual_shape(tmp_path):
    dataset = reviewed_demo_fixture(tmp_path)
    summary = validate_dataset(
        dataset, ValidationPolicy.reviewed_demo(), verify_state_files=False
    )
    assert summary["paper_count"] == 10
    assert summary["reviewed_case_count"] == 80
    assert summary["paper_split_ratios"] == {
        "analysis": 0.6, "validation": 0.2, "test": 0.2,
    }


def test_reviewed_demo_profile_rejects_false_review_claim_and_missing_case(tmp_path):
    dataset = reviewed_demo_fixture(tmp_path)
    manifest = json.loads((dataset / MANIFEST_FILE).read_text(encoding="utf-8"))
    manifest["review_claim"] = "Expert-reviewed benchmark."
    (dataset / MANIFEST_FILE).write_text(
        json.dumps(manifest) + "\n", encoding="utf-8"
    )
    case_lines = (dataset / CASES_FILE).read_text(encoding="utf-8").splitlines()
    (dataset / CASES_FILE).write_text(
        "\n".join(case_lines[:-1]) + "\n", encoding="utf-8"
    )
    with pytest.raises(DatasetValidationError) as error:
        validate_dataset(dataset, ValidationPolicy.reviewed_demo(), verify_state_files=False)
    assert "reviewed case count 79 must equal 80" in error.value.errors
    assert "reviewed demonstration set must disclose a non-expert review claim" in error.value.errors


def test_production_ratio_policy_and_unique_ids_are_enforced(tmp_path):
    with pytest.raises(DatasetValidationError, match="paper count"):
        validate_dataset(FIXTURE)
    dataset = copied_fixture(tmp_path)
    first = (dataset / CASES_FILE).read_text(encoding="utf-8").splitlines()[0]
    with (dataset / CASES_FILE).open("a", encoding="utf-8") as stream:
        stream.write(first + "\n")
    with pytest.raises(DatasetValidationError, match="case IDs must be unique"):
        validate_dataset(dataset, ValidationPolicy.fixture())


def test_case_cannot_cross_its_paper_split(tmp_path):
    dataset = copied_fixture(tmp_path)
    lines = (dataset / CASES_FILE).read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[0])
    row["split"] = "validation"
    lines[0] = json.dumps(row)
    (dataset / CASES_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(DatasetValidationError, match="case/paper split mismatch"):
        validate_dataset(dataset, ValidationPolicy.fixture())


def test_schema_rejects_unanswerable_evidence_and_non_test_freeze():
    with pytest.raises(ValueError, match="unanswerable"):
        EvaluationCase(
            id="bad", paper_id="paper", split="analysis", language="en",
            question="Unknown?", answerable=False, relevant_chunk_ids=["c1"],
            minimum_evidence_sets=[], distractor_type="not_in_paper",
        )
    with pytest.raises(ValueError, match="only test"):
        EvaluationCase(
            id="bad", paper_id="paper", split="analysis", language="en",
            question="Known?", answerable=True, relevant_chunk_ids=["c1"],
            minimum_evidence_sets=[["c1"]], distractor_type="none", frozen=True,
        )


def test_validator_detects_illegal_chunk_reference(tmp_path):
    dataset = copied_fixture(tmp_path)
    lines = (dataset / CASES_FILE).read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[0])
    row["relevant_chunk_ids"] = ["missing"]
    row["minimum_evidence_sets"] = [["missing"]]
    lines[0] = json.dumps(row)
    (dataset / CASES_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(DatasetValidationError, match="invalid chunk"):
        validate_dataset(dataset, ValidationPolicy.fixture())


def test_freeze_is_content_addressed_and_cannot_repeat(tmp_path):
    dataset = copied_fixture(tmp_path)
    summary = freeze_test_split(dataset, ValidationPolicy.fixture())
    assert len(summary["sha256"]) == 64
    assert summary["test_case_count"] == 1
    assert validate_dataset(dataset, ValidationPolicy.fixture())["test_frozen"] is True
    with pytest.raises(ValueError, match="already frozen"):
        freeze_test_split(dataset, ValidationPolicy.fixture())


def test_frozen_digest_detects_annotation_changes(tmp_path):
    dataset = copied_fixture(tmp_path)
    freeze_test_split(dataset, ValidationPolicy.fixture())
    lines = (dataset / CASES_FILE).read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[0])
    row["question"] += " changed"
    lines[0] = json.dumps(row)
    (dataset / CASES_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(DatasetValidationError, match="frozen test digest mismatch"):
        validate_dataset(dataset, ValidationPolicy.fixture())


def test_candidate_generation_writes_human_review_template(tmp_path):
    output = tmp_path / "generated"
    papers, cases = generate_dataset(
        [FIXTURE / "state.json"], output,
        dataset_version="candidate-v1", split_seed="seed", questions_per_paper=12,
        settings=settings(), client=CandidateClient(),
    )
    assert len(papers) == len(cases) == 1
    assert cases[0].review_status == ReviewStatus.CANDIDATE
    assert "HUMAN REVIEW REQUIRED" in cases[0].reviewer_notes
    assert "The anonymized method" not in (output / CASES_FILE).read_text(encoding="utf-8")


def test_threshold_boundaries_apply_separately():
    case = EvaluationCase(
        id="boundary", paper_id="paper", split="validation", language="en",
        question="question", answerable=True, relevant_chunk_ids=["right"],
        minimum_evidence_sets=[["right"]], distractor_type="none", review_status="reviewed",
    )
    row = RawCase(
        case=case, hybrid_ids=["right", "noise"], candidate_ids=["right", "noise"],
        candidate_sections={"right": None, "noise": None},
        reranker_scores={"right": 0.5, "noise": 0.3}, latency_ms=1, reranker_latency_ms=0.2,
        degraded_reason=None, bm25_candidates=2, vector_candidates_raw=2,
        vector_candidates_filtered=2, vector_candidates_removed=0, rrf_candidates=2,
    )
    included = _observations([row], rerank=True, evidence_threshold=0.5, answerability_threshold=0.5)[0]
    assert included.retrieved_chunk_ids == ["right"]
    refused = _observations([row], rerank=True, evidence_threshold=0.5, answerability_threshold=0.5001)[0]
    assert refused.refused is True


def test_shadow_logging_uses_question_digest_and_rank_metrics(tmp_path, caplog):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"document": {"chunks": [
        {"chunk_id": "first", "text": "target first"},
        {"chunk_id": "second", "text": "target second"},
    ]}}), encoding="utf-8")

    class ReverseReranker(BaseReranker):
        model_name = "reverse"

        def score(self, query, passages, timeout):
            return [0.1, 0.9]

    service = AskPaperRetrievalService(
        settings(embedding_provider="mock", ask_reranker_mode="shadow"),
        reranker=ReverseReranker(),
    )
    caplog.set_level("INFO", logger="backend.ask_retrieval")
    result = service.retrieve("paper", str(state), "private complete question target")
    assert result.diagnostics.reranker_rank_changes == 2
    assert "query_sha256=" in caplog.text
    assert "private complete question target" not in caplog.text


def test_calibration_uses_validation_only_and_frozen_gate_is_single_use(tmp_path):
    dataset = copied_fixture(tmp_path)
    calibration_path = tmp_path / "calibration-v1.json"
    artifact = calibrate(
        dataset, calibration_path, "cal-v1", settings=settings(),
        vector_thresholds=[0], evidence_thresholds=[0.5, 0.9],
        answerability_thresholds=[0.5], service_factory=factory,
    )
    assert artifact["split_used"] == "validation"
    assert artifact["selected_validation_report"]["case_count"] == 1
    assert all(case["case_id"] == "sample-validation" for case in artifact["selected_validation_report"]["cases"])
    with pytest.raises(ValueError, match="direct test"):
        collect_raw(dataset, Split.TEST, settings(), service_factory=factory)

    freeze_test_split(dataset, ValidationPolicy.fixture())
    report_path = tmp_path / "frozen-report.json"
    report = run_frozen_test(
        dataset, calibration_path, report_path, settings=settings(), service_factory=factory,
    )
    assert report["dataset_version"] == "anonymized-fixture-v1"
    assert report_path.exists()
    with pytest.raises(ValueError, match="already been run"):
        run_frozen_test(
            dataset, calibration_path, tmp_path / "second.json", settings=settings(), service_factory=factory,
        )


def test_model_or_chunking_change_invalidates_calibration(tmp_path):
    dataset = copied_fixture(tmp_path)
    calibration_path = tmp_path / "calibration-v1.json"
    calibrate(
        dataset, calibration_path, "cal-v1", settings=settings(),
        vector_thresholds=[0], evidence_thresholds=[0.5],
        answerability_thresholds=[0.5], service_factory=factory,
    )
    freeze_test_split(dataset, ValidationPolicy.fixture())
    with pytest.raises(ValueError, match="create a new calibration version"):
        run_frozen_test(
            dataset, calibration_path, tmp_path / "report.json",
            settings=settings(embedding_model="changed-model"), service_factory=factory,
        )
