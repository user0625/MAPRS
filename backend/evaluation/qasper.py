from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


QASPER_ADAPTER_VERSION: Literal["qasper-adapter-v1"] = "qasper-adapter-v1"
QASPER_LICENSE = "CC BY 4.0"
QASPER_ATTRIBUTION_URL = "https://allenai.org/data/qasper"
QASPER_STATE_SCHEMA = "qasper-retrieval-state-v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(raw)
    try:
        with open(descriptor, "w", encoding="utf-8", closefd=True) as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _normalized_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


class QasperChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    paper_id: str
    section: str
    section_index: int = Field(ge=0)
    paragraph_index: int = Field(ge=0)
    text: str = Field(min_length=1)


class QasperCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    paper_id: str
    split: Literal["train", "validation", "test"]
    question: str = Field(min_length=1)
    answerable: bool
    relevant_chunk_ids: list[str] = Field(default_factory=list)
    minimum_evidence_sets: list[list[str]] = Field(default_factory=list)
    answer_type: Literal["extractive", "yes_no", "free_form", "unanswerable"]
    gold_answers: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def evidence_is_consistent(self) -> "QasperCase":
        relevant = set(self.relevant_chunk_ids)
        if self.answerable:
            if not relevant or not self.minimum_evidence_sets:
                raise ValueError("answerable QASPER cases require mapped evidence")
            if any(not group or not set(group) <= relevant for group in self.minimum_evidence_sets):
                raise ValueError("minimum evidence must be a non-empty subset of relevant evidence")
        elif relevant or self.minimum_evidence_sets:
            raise ValueError("unanswerable QASPER cases cannot contain evidence")
        return self


class QasperPaper(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper_id: str
    title: str = ""
    chunks: list[QasperChunk]
    cases: list[QasperCase]


class QasperAdaptation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["qasper-adapter-v1"] = QASPER_ADAPTER_VERSION
    split: Literal["train", "validation", "test"]
    papers: list[QasperPaper]
    exclusions: dict[str, int] = Field(default_factory=dict)


def qasper_state(paper: QasperPaper) -> dict[str, Any]:
    """Return the minimal production-retrieval state for one QASPER paper.

    QASPER has no trustworthy PDF page mapping in this adapter. Page fields are
    intentionally absent rather than synthesized.
    """
    return {
        "schema_version": QASPER_STATE_SCHEMA,
        "document": {
            "metadata": {"paper_id": paper.paper_id, "title": paper.title},
            "chunks": [
                {
                    "chunk_id": chunk.chunk_id,
                    "paper_id": chunk.paper_id,
                    "section": chunk.section,
                    "section_index": chunk.section_index,
                    "paragraph_index": chunk.paragraph_index,
                    "text": chunk.text,
                }
                for chunk in paper.chunks
            ],
        },
    }


def materialize_qasper_states(
    adapted: QasperAdaptation, cache_dir: Path,
) -> dict[str, dict[str, str]]:
    """Write content-addressed state files and a stable paper-to-state index."""
    index: dict[str, dict[str, str]] = {}
    for paper in adapted.papers:
        state = qasper_state(paper)
        digest = hashlib.sha256(_canonical_bytes(state)).hexdigest()
        relative = Path("states") / f"{digest}.json"
        destination = cache_dir / relative
        if not destination.exists():
            _atomic_json(destination, state)
        elif hashlib.sha256(_canonical_bytes(json.loads(destination.read_text(encoding="utf-8")))).hexdigest() != digest:
            raise ValueError(f"QASPER state digest collision for paper {paper.paper_id}")
        index[paper.paper_id] = {"path": relative.as_posix(), "state_sha256": digest}
    index_path = cache_dir / f"state-index-{adapted.split}.json"
    _atomic_json(index_path, index)
    return index


def load_qasper_state_index(
    cache_dir: Path, split: Literal["train", "validation", "test"],
) -> dict[str, dict[str, str]]:
    path = cache_dir / f"state-index-{split}.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"QASPER {split} state index is invalid")
    for paper_id, item in value.items():
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise ValueError(f"QASPER {split} state index is invalid")
        state_path = cache_dir / item["path"]
        state = json.loads(state_path.read_text(encoding="utf-8"))
        digest = hashlib.sha256(_canonical_bytes(state)).hexdigest()
        chunks = (state.get("document") or {}).get("chunks", []) if isinstance(state, dict) else []
        has_page_claim = any(
            isinstance(chunk, dict) and ({"page_start", "page_end"} & set(chunk))
            for chunk in chunks
        )
        if digest != item.get("state_sha256") or has_page_claim:
            raise ValueError(f"QASPER state version/SHA mismatch for paper {paper_id}")
    return value


def _iter_papers(raw: Any) -> Iterable[tuple[str, dict[str, Any]]]:
    """Accept the official paper-id map and common exported list forms."""
    if isinstance(raw, dict) and isinstance(raw.get("papers"), list):
        raw = raw["papers"]
    if isinstance(raw, list):
        for index, paper in enumerate(raw):
            if isinstance(paper, dict):
                paper_id = str(paper.get("paper_id") or paper.get("id") or index)
                yield paper_id, paper
        return
    if isinstance(raw, dict):
        for paper_id, paper in raw.items():
            if isinstance(paper, dict):
                yield str(paper.get("paper_id") or paper_id), paper


def _sections(paper: dict[str, Any]) -> list[dict[str, Any]]:
    full_text = paper.get("full_text") or []
    if isinstance(full_text, dict):
        names = full_text.get("section_name") or []
        paragraphs = full_text.get("paragraphs") or []
        return [
            {"section_name": names[index] if index < len(names) else "", "paragraphs": value}
            for index, value in enumerate(paragraphs)
        ]
    return [item for item in full_text if isinstance(item, dict)]


def _questions(paper: dict[str, Any]) -> list[dict[str, Any]]:
    rows = paper.get("qas") or paper.get("questions") or []
    if isinstance(rows, dict):
        questions = rows.get("question") or []
        ids = rows.get("question_id") or rows.get("id") or []
        answers = rows.get("answers") or []
        return [
            {
                "question": value,
                "question_id": ids[index] if index < len(ids) else str(index),
                "answers": answers[index] if index < len(answers) else [],
            }
            for index, value in enumerate(questions)
        ]
    return [item for item in rows if isinstance(item, dict)]


def _annotation_payload(annotation: dict[str, Any]) -> dict[str, Any]:
    answer = annotation.get("answer")
    return answer if isinstance(answer, dict) else annotation


def _answer_type(
    payload: dict[str, Any],
) -> Literal["extractive", "yes_no", "free_form", "unanswerable"]:
    if bool(payload.get("unanswerable")):
        return "unanswerable"
    if payload.get("yes_no") is not None:
        return "yes_no"
    if payload.get("extractive_spans"):
        return "extractive"
    return "free_form"


def _gold_texts(payload: dict[str, Any]) -> list[str]:
    kind = _answer_type(payload)
    if kind == "unanswerable":
        return []
    if kind == "yes_no":
        value = payload.get("yes_no")
        return ["yes" if value is True else "no"]
    if kind == "extractive":
        return [text for value in payload.get("extractive_spans", []) if (text := _normalized_text(value))]
    value = _normalized_text(payload.get("free_form_answer"))
    return [value] if value else []


def _map_evidence(
    evidence: list[Any], candidates: dict[str, list[str]],
) -> tuple[list[str] | None, str | None]:
    texts = [_normalized_text(item) for item in evidence if _normalized_text(item)]
    if any("FLOAT SELECTED" in text.upper() for text in texts):
        return None, "figure_or_table"
    if not texts:
        return None, "missing_evidence"
    counts = Counter(texts)
    mapped: list[str] = []
    for text, required in counts.items():
        matches = candidates.get(text, [])
        # QASPER evidence stores paragraph text, not a location. A single copy of
        # duplicated text is ambiguous; repeated evidence can be resolved only
        # when it accounts for every occurrence in stable document order.
        if not matches:
            return None, "unmapped_evidence"
        if len(matches) != required and len(matches) > 1:
            return None, "ambiguous_duplicate_evidence"
        if required > len(matches):
            return None, "unmapped_evidence"
        mapped.extend(matches[:required])
    return list(dict.fromkeys(mapped)), None


def adapt_qasper(raw: Any, split: Literal["train", "validation", "test"]) -> QasperAdaptation:
    papers: list[QasperPaper] = []
    excluded: Counter[str] = Counter()
    for paper_id, raw_paper in _iter_papers(raw):
        chunks: list[QasperChunk] = []
        by_text: dict[str, list[str]] = defaultdict(list)
        for section_index, section in enumerate(_sections(raw_paper)):
            section_name = _normalized_text(section.get("section_name")) or "Untitled"
            paragraphs = section.get("paragraphs") or []
            if isinstance(paragraphs, str):
                paragraphs = [paragraphs]
            for paragraph_index, paragraph in enumerate(paragraphs):
                text = _normalized_text(paragraph)
                if not text:
                    continue
                chunk_id = f"qasper:{paper_id}:s{section_index}:p{paragraph_index}"
                chunks.append(QasperChunk(
                    chunk_id=chunk_id, paper_id=paper_id, section=section_name,
                    section_index=section_index, paragraph_index=paragraph_index, text=text,
                ))
                by_text[text].append(chunk_id)
        cases: list[QasperCase] = []
        for question_index, qa in enumerate(_questions(raw_paper)):
            annotations = [item for item in (qa.get("answers") or []) if isinstance(item, dict)]
            if not annotations:
                excluded["missing_annotations"] += 1
                continue
            payloads = [_annotation_payload(item) for item in annotations]
            flags = {bool(item.get("unanswerable")) for item in payloads}
            if len(flags) != 1:
                excluded["answerability_disagreement"] += 1
                continue
            answerable = not next(iter(flags))
            minimum_sets: list[list[str]] = []
            exclusion: str | None = None
            if answerable:
                for annotation, payload in zip(annotations, payloads):
                    evidence = payload.get("evidence")
                    if evidence is None:
                        evidence = annotation.get("evidence") or []
                    mapped, exclusion = _map_evidence(list(evidence or []), by_text)
                    if exclusion:
                        break
                    assert mapped is not None
                    minimum_sets.append(mapped)
            if exclusion:
                excluded[exclusion] += 1
                continue
            question = _normalized_text(qa.get("question"))
            if not question:
                excluded["blank_question"] += 1
                continue
            answer_types = [_answer_type(item) for item in payloads]
            answer_type = "unanswerable" if not answerable else Counter(answer_types).most_common(1)[0][0]
            gold_answers = list(dict.fromkeys(
                text for payload in payloads for text in _gold_texts(payload)
            ))
            case_id = str(qa.get("question_id") or qa.get("id") or f"{paper_id}-{question_index}")
            relevant = list(dict.fromkeys(chunk for group in minimum_sets for chunk in group))
            cases.append(QasperCase(
                case_id=case_id, paper_id=paper_id, split=split, question=question,
                answerable=answerable, relevant_chunk_ids=relevant,
                minimum_evidence_sets=minimum_sets, answer_type=answer_type,
                gold_answers=gold_answers,
            ))
        if cases:
            papers.append(QasperPaper(
                paper_id=paper_id, title=_normalized_text(raw_paper.get("title")),
                chunks=chunks, cases=cases,
            ))
    return QasperAdaptation(split=split, papers=papers, exclusions=dict(sorted(excluded.items())))


def import_qasper(
    source: str, cache_dir: Path, split: Literal["train", "validation", "test"],
    *, expected_sha256: str,
) -> dict[str, Any]:
    """Import one official split into an ignored cache with content verification."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    source_path = cache_dir / f"source-{split}.json"
    if source.startswith(("https://", "http://")):
        with urllib.request.urlopen(source, timeout=120) as response, source_path.open("wb") as output:
            shutil.copyfileobj(response, output)
    else:
        supplied = Path(source)
        if not supplied.is_file():
            raise FileNotFoundError(supplied)
        if supplied.resolve() != source_path.resolve():
            shutil.copyfile(supplied, source_path)
    actual_sha = sha256_file(source_path)
    if actual_sha != expected_sha256.lower():
        source_path.unlink(missing_ok=True)
        raise ValueError(f"QASPER SHA-256 mismatch: expected {expected_sha256}, got {actual_sha}")
    raw = json.loads(source_path.read_text(encoding="utf-8"))
    adapted = adapt_qasper(raw, split)
    adapted_path = cache_dir / f"adapted-{split}.json"
    adapted_path.write_text(adapted.model_dump_json(indent=2) + "\n", encoding="utf-8")
    state_index = materialize_qasper_states(adapted, cache_dir)
    manifest_path = cache_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    manifest.update({
        "schema_version": QASPER_ADAPTER_VERSION,
        "license": QASPER_LICENSE,
        "attribution_url": QASPER_ATTRIBUTION_URL,
        "notice": "QASPER licensing does not grant or declare a license for this repository.",
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    })
    splits = manifest.setdefault("splits", {})
    current_papers = {paper.paper_id for paper in adapted.papers}
    for other_split in ("train", "validation", "test"):
        if other_split == split or other_split not in splits:
            continue
        other_path = cache_dir / f"adapted-{other_split}.json"
        if other_path.exists():
            other = QasperAdaptation.model_validate_json(other_path.read_text(encoding="utf-8"))
            overlap = current_papers & {paper.paper_id for paper in other.papers}
            if overlap:
                raise ValueError(
                    f"QASPER paper-level split leakage between {split} and {other_split}: "
                    f"{sorted(overlap)[:3]}"
                )
    splits[split] = {
        "source_sha256": actual_sha,
        "adapted_sha256": sha256_file(adapted_path),
        "state_index_sha256": sha256_file(cache_dir / f"state-index-{split}.json"),
        "paper_count": len(adapted.papers),
        "case_count": sum(len(paper.cases) for paper in adapted.papers),
        "state_count": len(state_index),
        "exclusions": adapted.exclusions,
    }
    _atomic_json(manifest_path, manifest)
    return splits[split]


def load_adapted(cache_dir: Path, split: Literal["train", "validation", "test"]) -> QasperAdaptation:
    manifest = json.loads((cache_dir / "manifest.json").read_text(encoding="utf-8"))
    path = cache_dir / f"adapted-{split}.json"
    expected = (manifest.get("splits") or {}).get(split, {}).get("adapted_sha256")
    if not expected or sha256_file(path) != expected:
        raise ValueError(f"QASPER {split} cache version/SHA mismatch")
    adapted = QasperAdaptation.model_validate_json(path.read_text(encoding="utf-8"))
    index_path = cache_dir / f"state-index-{split}.json"
    expected_index = (manifest.get("splits") or {}).get(split, {}).get("state_index_sha256")
    if expected_index:
        if not index_path.exists() or sha256_file(index_path) != expected_index:
            raise ValueError(f"QASPER {split} state index version/SHA mismatch")
        load_qasper_state_index(cache_dir, split)
    return adapted
