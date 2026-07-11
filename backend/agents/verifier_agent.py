from __future__ import annotations

from pydantic import BaseModel, Field

from backend.agents.base_agent import BaseAgent
from backend.schemas.agent_io import EvidenceBundle
from backend.schemas.report import FinalReport


class VerificationIssue(BaseModel):
  category: str
  section: str | None = None
  description: str
  evidence_ids: list[str] = Field(default_factory=list)
  severity: str = "warning"


class VerifierResult(BaseModel):
  accuracy: int = Field(ge=0, le=100)
  completeness: int = Field(ge=0, le=100)
  faithfulness: int = Field(ge=0, le=100)
  citation_validity: int = Field(ge=0, le=100)
  critical_depth: int = Field(ge=0, le=100)
  overall: int = Field(ge=0, le=100)
  passed: bool
  issues: list[VerificationIssue] = Field(default_factory=list)
  revision_instructions: list[str] = Field(default_factory=list)
  user_warning: str | None = None


class VerifierInput(BaseModel):
  report: FinalReport
  evidence_bundle: EvidenceBundle | None = None
  deterministic_issues: list[str] = Field(default_factory=list)
  pass_score: int = 75
  citation_score: int = 80


class VerifierAgent(BaseAgent):
  def __init__(self, llm_client) -> None:
    super().__init__("verifier_agent", llm_client)

  def run(self, agent_input: VerifierInput) -> VerifierResult:
    if self.is_mock:
      raise RuntimeError("Mock verification must use the deterministic quality gate.")
    evidence = []
    for item in agent_input.evidence_bundle.items if agent_input.evidence_bundle else []:
      evidence.append({"evidence_id": item.evidence_id, "chunk_id": item.chunk_id,
        "page_start": item.page_start, "page_end": item.page_end,
        "section": item.section, "text": item.text[:1800]})
    system = self.prompt_loader.render("verifier_system.md")
    prompt = self.prompt_loader.render("verifier_user.md",
      report=agent_input.report.model_dump(mode="json"), evidence=evidence,
      deterministic_issues=agent_input.deterministic_issues,
      pass_score=agent_input.pass_score, citation_score=agent_input.citation_score,
      schema_instruction=self.build_schema_instruction(VerifierResult))
    return self.generate_pydantic(prompt, VerifierResult, system_prompt=system,
                                  temperature=0.0, max_tokens=3500)
