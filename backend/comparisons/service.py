from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from backend.api.comparison_store import ComparisonEvidence, ComparisonPaper, ComparisonStore
from backend.core.config import AppSettings


DIMENSIONS: list[tuple[str, tuple[str, ...]]] = [
    ("research_question", ("objective", "problem", "aim", "research question", "目标", "问题")),
    ("dataset", ("dataset", "cohort", "sample", "benchmark", "数据集", "队列", "样本")),
    ("method", ("method", "model", "architecture", "framework", "方法", "模型", "架构")),
    ("baseline", ("baseline", "state-of-the-art", "comparison", "基线", "对比")),
    ("metrics", ("metric", "accuracy", "auc", "f1", "c-index", "指标", "准确率")),
    ("results", ("result", "outperform", "improve", "performance", "结果", "提升", "性能")),
    ("limitations", ("limitation", "future work", "however", "局限", "未来工作")),
]


def _words(text: str) -> list[str]:
    return [word.casefold() for word in re.findall(r"[\w\u4e00-\u9fff-]+", text) if len(word) > 1]


def _short(text: str, limit: int = 520) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[: limit - 1].rstrip() + "…"


def _load_state(paper: ComparisonPaper) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    path = Path(paper.state_json_path)
    if not path.is_file():
        raise ValueError(f"Source state is unavailable for task {paper.source_task_id}.")
    state = json.loads(path.read_text(encoding="utf-8"))
    chunks = [item for item in ((state.get("document") or {}).get("chunks") or []) if isinstance(item, dict) and item.get("text")]
    if not chunks:
        raise ValueError(f"Source state has no chunks for task {paper.source_task_id}.")
    return state, chunks


def _select_chunks(chunks: list[dict[str, Any]], focus: str, max_items: int, token_budget: int) -> list[tuple[dict[str, Any], float, list[str]]]:
    focus_words = _words(focus)
    ranked: list[tuple[float, int, dict[str, Any], list[str]]] = []
    for index, chunk in enumerate(chunks):
        haystack = f"{chunk.get('section') or ''} {chunk.get('text') or ''}".casefold()
        dimensions = [name for name, terms in DIMENSIONS if any(term in haystack for term in terms)]
        keyword_score = sum(haystack.count(term) for _, terms in DIMENSIONS for term in terms)
        focus_score = sum(3 * haystack.count(word) for word in focus_words)
        page_bonus = 0.2 if chunk.get("page_start") == 1 else 0
        ranked.append((keyword_score + focus_score + page_bonus, -index, chunk, dimensions))
    ranked.sort(reverse=True, key=lambda item: (item[0], item[1]))
    selected: list[tuple[dict[str, Any], float, list[str]]] = []
    chars = 0
    for score, _, chunk, dimensions in ranked:
        text = str(chunk.get("text") or "")
        if selected and chars + len(text) > token_budget * 4:
            continue
        selected.append((chunk, score, dimensions))
        chars += len(text)
        if len(selected) >= max_items:
            break
    return selected


def _sanitize_citations(payload: Any, whitelist: set[str]) -> int:
    removed = 0
    if isinstance(payload, dict):
        for key, value in list(payload.items()):
            if key == "evidence_ids" and isinstance(value, list):
                clean = [item for item in value if isinstance(item, str) and item in whitelist]
                removed += len(value) - len(clean)
                payload[key] = clean
            else:
                removed += _sanitize_citations(value, whitelist)
    elif isinstance(payload, list):
        for item in payload:
            removed += _sanitize_citations(item, whitelist)
    return removed


def build_comparison(comparison_id: str, store: ComparisonStore, settings: AppSettings) -> tuple[dict[str, Any], list[ComparisonEvidence]]:
    record = store.get(comparison_id)
    if not record:
        raise ValueError("Comparison does not exist.")
    papers = store.papers(comparison_id)
    evidence: list[ComparisonEvidence] = []
    profiles: list[dict[str, Any]] = []
    dimension_cells: dict[str, list[dict[str, Any]]] = defaultdict(list)
    warnings: list[str] = []

    for paper in papers:
        _, chunks = _load_state(paper)
        selected = _select_chunks(chunks, record.focus, settings.comparison_evidence_per_paper, settings.comparison_paper_max_tokens)
        grouped: dict[str, list[ComparisonEvidence]] = defaultdict(list)
        for index, (chunk, score, dimensions) in enumerate(selected, 1):
            evidence_id = f"{comparison_id}:ev:{paper.position + 1:02d}:{index:02d}"
            item = ComparisonEvidence(
                evidence_id=evidence_id,
                comparison_id=comparison_id,
                source_task_id=paper.source_task_id,
                paper_id=paper.paper_id,
                paper_title=paper.title,
                chunk_id=str(chunk.get("chunk_id") or f"chunk-{index}"),
                page_start=chunk.get("page_start"),
                page_end=chunk.get("page_end"),
                section=chunk.get("section"),
                text=str(chunk.get("text"))[:4000],
                score=float(score),
            )
            evidence.append(item)
            for dimension in dimensions:
                grouped[dimension].append(item)
        dimensions_payload: dict[str, Any] = {}
        for dimension, _ in DIMENSIONS:
            items = grouped.get(dimension, [])[:2]
            if not items:
                warnings.append(f"{paper.source_task_id}: no evidence for {dimension}")
                dimensions_payload[dimension] = {"summary": "Evidence not found in the retained budget.", "evidence_ids": []}
            else:
                dimensions_payload[dimension] = {"summary": _short(" ".join(item.text for item in items)), "evidence_ids": [item.evidence_id for item in items]}
            dimension_cells[dimension].append({"source_task_id": paper.source_task_id, **dimensions_payload[dimension]})
        profiles.append({
            "source_task_id": paper.source_task_id,
            "paper_id": paper.paper_id,
            "title": paper.title,
            "authors": paper.authors,
            "year": paper.year,
            "dimensions": dimensions_payload,
            "evidence_ids": [item.evidence_id for item in evidence if item.source_task_id == paper.source_task_id],
        })

    matrix = [{"dimension": dimension, "cells": dimension_cells[dimension]} for dimension, _ in DIMENSIONS]
    all_ids = [item.evidence_id for item in evidence]
    synthesis_names = ["commonalities", "differences", "conflicts", "method_evolution", "applicability"]
    synthesis: dict[str, Any] = {}
    for index, name in enumerate(synthesis_names):
        ids = all_ids[index::max(1, len(synthesis_names))][: min(5, len(papers))]
        language_text = {
            "commonalities": "各论文围绕相近研究问题形成了可比较的方法与实验脉络。",
            "differences": "论文在数据、方法设计、基线与报告指标上存在差异。",
            "conflicts": "保留证据中未确认直接冲突；数值不可在不同数据设定下直接横比。",
            "method_evolution": "按发表年份与方法证据可观察模型设计和评估范围的演进。",
            "applicability": "适用场景取决于数据可得性、指标目标及各论文明确报告的局限。",
        } if record.language == "zh" else {
            "commonalities": "The papers form a comparable thread around related questions, methods, and experiments.",
            "differences": "They differ in data, method design, baselines, and reported metrics.",
            "conflicts": "No direct conflict is established by retained evidence; results across different settings are not directly comparable.",
            "method_evolution": "Publication years and method evidence indicate an evolution in design and evaluation scope.",
            "applicability": "Applicability depends on data availability, target metrics, and explicitly reported limitations.",
        }
        synthesis[name] = {"content": language_text[name], "evidence_ids": ids}
    structured = {
        "schema_version": "paper-comparison-v1",
        "comparison_id": comparison_id,
        "title": record.title,
        "focus": record.focus,
        "language": record.language,
        "source_papers": [{"source_task_id": p.source_task_id, "paper_id": p.paper_id, "title": p.title, "authors": p.authors, "year": p.year} for p in papers],
        "profiles": profiles,
        "matrix": matrix,
        "synthesis": synthesis,
        "claims": [{"text": value["content"], "evidence_ids": value["evidence_ids"]} for value in synthesis.values()],
        "evidence_ids": all_ids,
        "quality_warnings": sorted(set(warnings)),
        "budgets": {"per_paper_tokens": settings.comparison_paper_max_tokens, "final_tokens": settings.comparison_final_max_tokens, "evidence_per_paper": settings.comparison_evidence_per_paper},
    }
    removed = _sanitize_citations(structured, set(all_ids))
    if removed:
        structured["quality_warnings"].append(f"Removed {removed} non-whitelisted citations.")
    return structured, evidence


def to_markdown(report: dict[str, Any]) -> str:
    lines = [f"# {report['title']}", "", f"**Focus:** {report['focus']}", "", "## Source papers", ""]
    for paper in report["source_papers"]:
        year = f" ({paper['year']})" if paper.get("year") else ""
        lines.append(f"- {paper['title']}{year} — `{paper['source_task_id']}`")
    lines.extend(["", "## Comparison matrix", ""])
    titles = [paper["title"] for paper in report["source_papers"]]
    lines.append("| Dimension | " + " | ".join(titles) + " |")
    lines.append("| --- | " + " | ".join("---" for _ in titles) + " |")
    for row in report["matrix"]:
        cells = [f"{cell['summary']} {' '.join(f'[{eid}]' for eid in cell['evidence_ids'])}".replace("|", "\\|") for cell in row["cells"]]
        lines.append(f"| {row['dimension']} | " + " | ".join(cells) + " |")
    lines.extend(["", "## Synthesis", ""])
    for name, section in report["synthesis"].items():
        lines.extend([f"### {name.replace('_', ' ').title()}", "", section["content"], "", "Evidence: " + ", ".join(section["evidence_ids"]), ""])
    if report["quality_warnings"]:
        lines.extend(["## Quality warnings", ""] + [f"- {item}" for item in report["quality_warnings"]])
    return "\n".join(lines).rstrip() + "\n"
