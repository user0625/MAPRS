from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SCHEMA_VERSION = "paper-eval-v1"
MANIFEST_FILE = "manifest.json"
PAPERS_FILE = "papers.jsonl"
CASES_FILE = "cases.jsonl"
FREEZE_FILE = "frozen-test.json"


class Split(str, Enum):
    ANALYSIS = "analysis"
    VALIDATION = "validation"
    TEST = "test"


class ReviewStatus(str, Enum):
    CANDIDATE = "candidate"
    REVIEWED = "reviewed"
    REJECTED = "rejected"


class DistractorType(str, Enum):
    SAME_SECTION = "same_section"
    CROSS_SECTION = "cross_section"
    SYNONYM = "synonym"
    NOT_IN_PAPER = "not_in_paper"
    ADJACENT_PARAGRAPH = "adjacent_paragraph"
    ABBREVIATION = "abbreviation"
    NUMERIC = "numeric"
    NEAR_UNANSWERABLE = "near_unanswerable"
    NONE = "none"


class PaperRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper_id: str = Field(min_length=1)
    state_path: str = Field(min_length=1)
    split: Split
    state_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    chunk_ids: list[str] = Field(min_length=1)

    @field_validator("paper_id", "state_path")
    @classmethod
    def clean_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("chunk_ids")
    @classmethod
    def unique_chunks(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("chunk_ids must be unique")
        return values


class EvaluationCase(BaseModel):
    """One human-reviewed question. Chunk text is deliberately not stored here."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    id: str = Field(min_length=1)
    paper_id: str = Field(min_length=1)
    split: Split
    language: Literal["zh", "en"]
    question: str = Field(min_length=1)
    section: str | None = None
    answerable: bool
    relevant_chunk_ids: list[str] = Field(default_factory=list)
    minimum_evidence_sets: list[list[str]] = Field(default_factory=list)
    distractor_type: DistractorType
    review_status: ReviewStatus = ReviewStatus.CANDIDATE
    reviewer_notes: str | None = None
    frozen: bool = False

    @field_validator("id", "paper_id", "question")
    @classmethod
    def clean_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("section", "reviewer_notes")
    @classmethod
    def clean_optional(cls, value: str | None) -> str | None:
        value = value.strip() if value else None
        return value or None

    @model_validator(mode="after")
    def evidence_matches_answerability(self) -> "EvaluationCase":
        relevant = set(self.relevant_chunk_ids)
        if len(relevant) != len(self.relevant_chunk_ids):
            raise ValueError("relevant_chunk_ids must be unique")
        if self.answerable:
            if not relevant or not self.minimum_evidence_sets:
                raise ValueError("answerable cases require relevant chunks and minimum evidence")
            for evidence_set in self.minimum_evidence_sets:
                if not evidence_set or len(evidence_set) != len(set(evidence_set)):
                    raise ValueError("minimum evidence sets must be non-empty and unique")
                if not set(evidence_set) <= relevant:
                    raise ValueError("minimum evidence must be a subset of relevant chunks")
        elif self.relevant_chunk_ids or self.minimum_evidence_sets:
            raise ValueError("unanswerable cases cannot contain evidence")
        if self.frozen and self.split != Split.TEST:
            raise ValueError("only test cases may be frozen")
        return self

    @property
    def is_multi_evidence(self) -> bool:
        return self.answerable and min(map(len, self.minimum_evidence_sets)) > 1


class DatasetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    dataset_version: str = Field(min_length=1)
    created_at: str
    split_seed: str = Field(min_length=1)
    chunking_config: dict[str, Any]
    test_frozen: bool = False
    frozen_test_sha256: str | None = None
    frozen_at: str | None = None
    dataset_label: str | None = None
    reviewer_count: int | None = Field(default=None, ge=1)
    review_claim: str | None = None

    @model_validator(mode="after")
    def freeze_fields_are_consistent(self) -> "DatasetManifest":
        fields = (self.frozen_test_sha256, self.frozen_at)
        if self.test_frozen != all(fields):
            raise ValueError("test freeze fields are inconsistent")
        return self


class ValidationPolicy(BaseModel):
    minimum_papers: int = 15
    minimum_cases: int = 180
    minimum_language_cases: int = 30
    unanswerable_min: float = 0.25
    unanswerable_max: float = 0.30
    multi_evidence_min: float = 0.20
    split_tolerance: float = 0.08
    exact_papers: int | None = None
    exact_cases: int | None = None
    exact_cases_per_paper: int | None = None
    exact_language_cases_per_paper: int | None = None
    require_review_disclosure: bool = False
    required_distractor_types: list[DistractorType] = Field(default_factory=list)

    @classmethod
    def fixture(cls) -> "ValidationPolicy":
        return cls(
            minimum_papers=1, minimum_cases=1, minimum_language_cases=0,
            unanswerable_min=0, unanswerable_max=1, multi_evidence_min=0,
            split_tolerance=1,
        )

    @classmethod
    def reviewed_demo(cls) -> "ValidationPolicy":
        """Policy for a 10-paper/80-question bilingual reviewed demonstration set."""
        return cls(
            minimum_papers=10, minimum_cases=80, minimum_language_cases=40,
            unanswerable_min=0.25, unanswerable_max=0.25,
            multi_evidence_min=0.20, split_tolerance=0.01,
            exact_papers=10, exact_cases=80, exact_cases_per_paper=8,
            exact_language_cases_per_paper=4,
            require_review_disclosure=True,
            required_distractor_types=[
                DistractorType.SYNONYM, DistractorType.CROSS_SECTION,
                DistractorType.ADJACENT_PARAGRAPH, DistractorType.ABBREVIATION,
                DistractorType.NUMERIC, DistractorType.NEAR_UNANSWERABLE,
            ],
        )


class DatasetValidationError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(items: Iterable[BaseModel | dict[str, Any]]) -> str:
    rows = [item.model_dump(mode="json") if isinstance(item, BaseModel) else item for item in items]
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def read_jsonl(path: Path, model: type[BaseModel]) -> list[Any]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                rows.append(model.model_validate_json(line))
            except Exception as exc:
                raise ValueError(f"{path}:{number}: {exc}") from exc
    return rows


def write_jsonl(path: Path, rows: Iterable[BaseModel]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(row.model_dump_json(exclude_none=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def load_dataset(directory: Path) -> tuple[DatasetManifest, list[PaperRecord], list[EvaluationCase]]:
    manifest = DatasetManifest.model_validate_json((directory / MANIFEST_FILE).read_text(encoding="utf-8"))
    papers = read_jsonl(directory / PAPERS_FILE, PaperRecord)
    cases = read_jsonl(directory / CASES_FILE, EvaluationCase)
    return manifest, papers, cases


def state_chunks(path: Path) -> tuple[str, list[dict[str, Any]]]:
    state = json.loads(path.read_text(encoding="utf-8"))
    document = state.get("document") or {}
    chunks = [item for item in document.get("chunks", []) if isinstance(item, dict)]
    metadata = document.get("metadata") or {}
    paper_id = str(metadata.get("paper_id") or path.stem).strip()
    if not chunks:
        raise ValueError(f"state contains no chunks: {path}")
    ids = [str(item.get("chunk_id") or "") for item in chunks]
    if any(not item for item in ids) or len(ids) != len(set(ids)):
        raise ValueError(f"state has blank or duplicate chunk IDs: {path}")
    return paper_id, chunks


def assigned_splits(paper_ids: list[str], seed: str) -> dict[str, Split]:
    ordered = sorted(
        set(paper_ids),
        key=lambda value: hashlib.sha256(f"{seed}:{value}".encode()).hexdigest(),
    )
    count = len(ordered)
    analysis_end = round(count * 0.6)
    validation_end = analysis_end + round(count * 0.2)
    return {
        paper_id: Split.ANALYSIS if index < analysis_end else (
            Split.VALIDATION if index < validation_end else Split.TEST
        )
        for index, paper_id in enumerate(ordered)
    }


def _split_ratios(papers: list[PaperRecord]) -> dict[str, float]:
    counts = Counter(item.split.value for item in papers)
    return {name: counts[name] / max(1, len(papers)) for name in ("analysis", "validation", "test")}


def validate_dataset(
    directory: Path,
    policy: ValidationPolicy | None = None,
    *,
    verify_state_files: bool = True,
) -> dict[str, Any]:
    policy = policy or ValidationPolicy()
    manifest, papers, cases = load_dataset(directory)
    errors: list[str] = []
    paper_by_id = {item.paper_id: item for item in papers}
    if len(paper_by_id) != len(papers):
        errors.append("paper IDs must be unique")
    case_ids = [item.id for item in cases]
    if len(case_ids) != len(set(case_ids)):
        errors.append("case IDs must be unique")
    if len(papers) < policy.minimum_papers:
        errors.append(f"paper count {len(papers)} is below {policy.minimum_papers}")
    reviewed = [item for item in cases if item.review_status == ReviewStatus.REVIEWED]
    if len(reviewed) < policy.minimum_cases:
        errors.append(f"reviewed case count {len(reviewed)} is below {policy.minimum_cases}")
    if policy.exact_papers is not None and len(papers) != policy.exact_papers:
        errors.append(f"paper count {len(papers)} must equal {policy.exact_papers}")
    if policy.exact_cases is not None and len(reviewed) != policy.exact_cases:
        errors.append(f"reviewed case count {len(reviewed)} must equal {policy.exact_cases}")
    if policy.exact_cases is not None and len(cases) != policy.exact_cases:
        errors.append(f"total case count {len(cases)} must equal {policy.exact_cases}")
    if policy.exact_cases_per_paper is not None:
        reviewed_by_paper = Counter(item.paper_id for item in reviewed)
        for paper in papers:
            if reviewed_by_paper[paper.paper_id] != policy.exact_cases_per_paper:
                errors.append(
                    f"{paper.paper_id}: reviewed case count {reviewed_by_paper[paper.paper_id]} "
                    f"must equal {policy.exact_cases_per_paper}"
                )
    if policy.exact_language_cases_per_paper is not None:
        languages_by_paper = Counter((item.paper_id, item.language) for item in reviewed)
        for paper in papers:
            for language in ("zh", "en"):
                count = languages_by_paper[(paper.paper_id, language)]
                if count != policy.exact_language_cases_per_paper:
                    errors.append(
                        f"{paper.paper_id}: {language} reviewed case count {count} must equal "
                        f"{policy.exact_language_cases_per_paper}"
                    )
    if policy.require_review_disclosure:
        if manifest.reviewer_count != 1:
            errors.append("reviewed demonstration set must disclose reviewer_count=1")
        if manifest.dataset_label != "human-reviewed demonstration set":
            errors.append(
                "reviewed demonstration set must use "
                "dataset_label='human-reviewed demonstration set'"
            )
        if not manifest.review_claim or "expert" in manifest.review_claim.casefold():
            errors.append("reviewed demonstration set must disclose a non-expert review claim")
    for case in cases:
        paper = paper_by_id.get(case.paper_id)
        if not paper:
            errors.append(f"{case.id}: unknown paper {case.paper_id}")
            continue
        if case.split != paper.split:
            errors.append(f"{case.id}: case/paper split mismatch")
        invalid = (set(case.relevant_chunk_ids) | {x for group in case.minimum_evidence_sets for x in group}) - set(paper.chunk_ids)
        if invalid:
            errors.append(f"{case.id}: invalid chunk references {sorted(invalid)}")
        if manifest.test_frozen and case.split == Split.TEST and not case.frozen:
            errors.append(f"{case.id}: frozen dataset has mutable test case")
    for paper in papers:
        if verify_state_files:
            state_path = Path(paper.state_path)
            if not state_path.is_absolute():
                state_path = directory / state_path
            if not state_path.is_file():
                errors.append(f"{paper.paper_id}: missing state file")
            elif file_sha256(state_path) != paper.state_sha256:
                errors.append(f"{paper.paper_id}: state digest changed")
    ratios = _split_ratios(papers)
    for split, target in (("analysis", 0.6), ("validation", 0.2), ("test", 0.2)):
        if abs(ratios[split] - target) > policy.split_tolerance:
            errors.append(f"paper split {split}={ratios[split]:.3f}, expected {target:.1f}")
    if reviewed:
        unanswerable = sum(not item.answerable for item in reviewed) / len(reviewed)
        if not policy.unanswerable_min <= unanswerable <= policy.unanswerable_max:
            errors.append(f"unanswerable ratio {unanswerable:.3f} is outside policy")
        multi = sum(item.is_multi_evidence for item in reviewed) / len(reviewed)
        if multi < policy.multi_evidence_min:
            errors.append(f"multi-evidence ratio {multi:.3f} is below {policy.multi_evidence_min:.3f}")
        languages = Counter(item.language for item in reviewed)
        for language in ("zh", "en"):
            if languages[language] < policy.minimum_language_cases:
                errors.append(f"{language} reviewed cases {languages[language]} below {policy.minimum_language_cases}")
        distractors = {item.distractor_type for item in reviewed}
        missing_distractors = set(policy.required_distractor_types) - distractors
        if missing_distractors:
            errors.append(
                "missing required distractor types "
                + str(sorted(item.value for item in missing_distractors))
            )
    expected_frozen = canonical_sha256(
        [item for item in cases if item.split == Split.TEST]
        + [item for item in papers if item.split == Split.TEST]
    )
    if manifest.test_frozen and manifest.frozen_test_sha256 != expected_frozen:
        errors.append("frozen test digest mismatch")
    if errors:
        raise DatasetValidationError(errors)
    return {
        "schema_version": manifest.schema_version,
        "dataset_version": manifest.dataset_version,
        "paper_count": len(papers),
        "case_count": len(cases),
        "reviewed_case_count": len(reviewed),
        "paper_split_ratios": ratios,
        "test_frozen": manifest.test_frozen,
        "frozen_test_sha256": manifest.frozen_test_sha256,
    }


def freeze_test_split(directory: Path, policy: ValidationPolicy | None = None) -> dict[str, Any]:
    manifest, papers, cases = load_dataset(directory)
    if manifest.test_frozen:
        raise ValueError("test split is already frozen")
    # Validate all annotations before changing files; state paths and digests are part of the lock.
    validate_dataset(directory, policy)
    pending = [
        item.id for item in cases
        if item.split == Split.TEST and item.review_status != ReviewStatus.REVIEWED
    ]
    if pending:
        raise ValueError(f"test split contains non-reviewed cases: {pending}")
    frozen_cases = [
        item.model_copy(update={"frozen": True}) if item.split == Split.TEST else item
        for item in cases
    ]
    digest = canonical_sha256(
        [item for item in frozen_cases if item.split == Split.TEST]
        + [item for item in papers if item.split == Split.TEST]
    )
    frozen_at = utc_now()
    updated = manifest.model_copy(update={
        "test_frozen": True, "frozen_test_sha256": digest, "frozen_at": frozen_at,
    })
    write_jsonl(directory / CASES_FILE, frozen_cases)
    (directory / MANIFEST_FILE).write_text(updated.model_dump_json(indent=2) + "\n", encoding="utf-8")
    summary = {
        "schema_version": SCHEMA_VERSION,
        "dataset_version": manifest.dataset_version,
        "frozen_at": frozen_at,
        "test_paper_count": sum(item.split == Split.TEST for item in papers),
        "test_case_count": sum(item.split == Split.TEST for item in frozen_cases),
        "sha256": digest,
    }
    (directory / FREEZE_FILE).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary
