from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.core.config import AppSettings, get_settings
from backend.evaluation.real_dataset import (
    CASES_FILE,
    MANIFEST_FILE,
    PAPERS_FILE,
    DatasetManifest,
    DistractorType,
    EvaluationCase,
    PaperRecord,
    ReviewStatus,
    assigned_splits,
    file_sha256,
    state_chunks,
    utc_now,
    write_jsonl,
)
from backend.llm.client import BaseLLMClient, create_llm_client


class CandidateQuestion(BaseModel):
    question: str = Field(min_length=1)
    language: Literal["zh", "en"]
    section: str | None = None
    answerable: bool
    relevant_chunk_ids: list[str] = Field(default_factory=list)
    minimum_evidence_sets: list[list[str]] = Field(default_factory=list)
    distractor_type: DistractorType


class CandidateBatch(BaseModel):
    candidates: list[CandidateQuestion]


SYSTEM_PROMPT = """You create candidate questions for a private scientific-paper retrieval benchmark.
Return JSON only and follow the supplied schema. Questions and evidence are candidates: a human must verify them.
Use only chunk IDs from the input. Do not copy long passages. Include answerable and deliberately unanswerable
questions, multi-chunk questions, same/cross-section distractors, and terminology paraphrases."""


def _prompt(paper_id: str, chunks: list[dict[str, Any]], count: int) -> str:
    compact = [
        {
            "chunk_id": item.get("chunk_id"),
            "section": item.get("section"),
            "page_start": item.get("page_start"),
            "text": str(item.get("text", ""))[:4000],
        }
        for item in chunks
    ]
    schema = CandidateBatch.model_json_schema()
    return (
        f"Generate {count} diverse candidate questions for paper {paper_id}. Aim for 25-30% unanswerable "
        "and at least 20% questions whose smallest sufficient evidence set has multiple chunks. "
        "For unanswerable questions return empty evidence arrays. Output one object matching this schema:\n"
        f"{json.dumps(schema, ensure_ascii=False)}\nChunks:\n"
        f"{json.dumps(compact, ensure_ascii=False)}"
    )


def generate_dataset(
    state_paths: list[Path],
    output: Path,
    *,
    dataset_version: str,
    split_seed: str,
    questions_per_paper: int = 13,
    settings: AppSettings | None = None,
    client: BaseLLMClient | None = None,
) -> tuple[list[PaperRecord], list[EvaluationCase]]:
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"output directory is not empty: {output}")
    if not 12 <= questions_per_paper <= 15:
        raise ValueError("questions_per_paper must be between 12 and 15")
    loaded: list[tuple[Path, str, list[dict[str, Any]]]] = []
    seen: set[str] = set()
    for raw_path in state_paths:
        path = raw_path.resolve()
        paper_id, chunks = state_chunks(path)
        if paper_id in seen:
            raise ValueError(f"duplicate paper ID: {paper_id}")
        seen.add(paper_id)
        loaded.append((path, paper_id, chunks))
    if not loaded:
        raise ValueError("at least one --state is required")
    splits = assigned_splits([item[1] for item in loaded], split_seed)
    settings = settings or get_settings()
    client = client or create_llm_client(settings)
    papers: list[PaperRecord] = []
    cases: list[EvaluationCase] = []
    for path, paper_id, chunks in loaded:
        chunk_ids = [str(item["chunk_id"]) for item in chunks]
        papers.append(PaperRecord(
            paper_id=paper_id,
            state_path=str(path),
            split=splits[paper_id],
            state_sha256=file_sha256(path),
            chunk_ids=chunk_ids,
        ))
        batch = client.generate_pydantic(
            _prompt(paper_id, chunks, questions_per_paper),
            CandidateBatch,
            system_prompt=SYSTEM_PROMPT,
            temperature=0.3,
            max_tokens=6000,
        )
        if not 1 <= len(batch.candidates) <= questions_per_paper:
            raise ValueError(f"model returned invalid candidate count for {paper_id}")
        for index, candidate in enumerate(batch.candidates, 1):
            cases.append(EvaluationCase(
                id=f"{paper_id}:q{index:02d}",
                paper_id=paper_id,
                split=splits[paper_id],
                language=candidate.language,
                question=candidate.question,
                section=candidate.section,
                answerable=candidate.answerable,
                relevant_chunk_ids=candidate.relevant_chunk_ids,
                minimum_evidence_sets=candidate.minimum_evidence_sets,
                distractor_type=candidate.distractor_type,
                review_status=ReviewStatus.CANDIDATE,
                reviewer_notes="HUMAN REVIEW REQUIRED: verify question, answerability, section, and minimum evidence.",
            ))
    output.mkdir(parents=True, exist_ok=True)
    manifest = DatasetManifest(
        dataset_version=dataset_version,
        created_at=utc_now(),
        split_seed=split_seed,
        chunking_config={"chunk_size": settings.chunk_size, "chunk_overlap": settings.chunk_overlap},
    )
    (output / MANIFEST_FILE).write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    write_jsonl(output / PAPERS_FILE, papers)
    write_jsonl(output / CASES_FILE, cases)
    return papers, cases


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate human-review templates for a private paper evaluation set.")
    parser.add_argument("--state", type=Path, action="append", required=True, help="Analysis state JSON; repeat per paper.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dataset-version", required=True)
    parser.add_argument("--split-seed", required=True)
    parser.add_argument("--questions-per-paper", type=int, default=13)
    args = parser.parse_args()
    papers, cases = generate_dataset(
        args.state, args.output, dataset_version=args.dataset_version,
        split_seed=args.split_seed, questions_per_paper=args.questions_per_paper,
    )
    print(json.dumps({"papers": len(papers), "candidate_cases": len(cases), "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
