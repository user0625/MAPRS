from __future__ import annotations

from backend.schemas.agent_io import EvidenceBundle
from backend.schemas.paper import PaperDocument
from backend.schemas.report import FinalReport, QualitySummary


class ReportQualityGate:
  """Deterministic citation validation used in mock and real workflows."""

  def __init__(self, pass_score: int = 75, citation_score: int = 80) -> None:
    self.pass_score = pass_score
    self.citation_score = citation_score

  def evaluate(self, report: FinalReport, evidence: EvidenceBundle | None,
               document: PaperDocument, revision_count: int = 0) -> QualitySummary:
    items = {item.evidence_id: item for item in (evidence.items if evidence else [])}
    chunks = {chunk.chunk_id: chunk for chunk in document.chunks}
    referenced: set[str] = set()
    issues: list[str] = []
    key_sections = 0
    cited_sections = 0
    for section in report.sections:
      ids = list(dict.fromkeys(section.evidence_ids))
      section.evidence_ids = ids
      if section.content.strip():
        key_sections += 1
        cited_sections += bool(ids)
      for evidence_id in ids:
        item = items.get(evidence_id)
        if item is None:
          issues.append(f"Unknown evidence ID: {evidence_id}")
          continue
        referenced.add(evidence_id)
        chunk = chunks.get(item.chunk_id)
        if chunk is None or chunk.paper_id != document.metadata.paper_id:
          issues.append(f"Evidence cannot be traced to this paper: {evidence_id}")
        elif (item.page_start != chunk.page_start or item.page_end != chunk.page_end
              or item.section != chunk.section):
          issues.append(f"Evidence location mismatch: {evidence_id}")
      for claim in section.claims:
        if not claim.evidence_ids:
          issues.append(f"Unsupported claim in {section.title}: {claim.text[:80]}")
    total_refs = sum(len(section.evidence_ids) for section in report.sections)
    valid_refs = sum(1 for section in report.sections for evidence_id in section.evidence_ids
                     if evidence_id in items and evidence_id in referenced)
    validity = round(100 * valid_refs / total_refs) if total_refs else 100
    coverage = cited_sections / key_sections if key_sections else 1.0
    faithfulness = max(0, 100 - 12 * len(issues))
    completeness = round(55 + 45 * coverage)
    accuracy = min(validity, faithfulness)
    critical = 85 if any(any(word in s.title.lower() for word in ("limit", "critic", "局限", "批判"))
                         for s in report.sections) else 65
    overall = round((accuracy + completeness + faithfulness + validity + critical) / 5)
    passed = not issues and overall >= self.pass_score and validity >= self.citation_score
    return QualitySummary(accuracy=accuracy, completeness=completeness,
      faithfulness=faithfulness, citation_validity=validity, critical_depth=critical,
      overall=overall, passed=passed, citation_coverage=coverage,
      revision_count=revision_count, issues=issues[:20])

  def sanitize(self, report: FinalReport, evidence: EvidenceBundle | None) -> None:
    valid = set(evidence.evidence_ids()) if evidence else set()
    for section in report.sections:
      section.evidence_ids = [item for item in dict.fromkeys(section.evidence_ids) if item in valid]
      for claim in section.claims:
        claim.evidence_ids = [item for item in dict.fromkeys(claim.evidence_ids) if item in valid]
