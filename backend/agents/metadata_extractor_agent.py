from __future__ import annotations

from pydantic import BaseModel, Field

from backend.agents.base_agent import BaseAgent
from backend.schemas.paper import PaperMetadata


class ExtractedMetadata(BaseModel):
  title: str | None = None
  authors: list[str] = Field(default_factory=list)
  abstract: str | None = None
  year: int | None = Field(default=None, ge=1900, le=2100)
  venue: str | None = None
  doi: str | None = None
  arxiv_id: str | None = None
  language: str | None = None
  keywords: list[str] = Field(default_factory=list)
  confidence: dict[str, float] = Field(default_factory=dict)


class MetadataExtractionInput(BaseModel):
  current_metadata: PaperMetadata
  first_page_text: str
  abstract_candidate: str | None = None
  section_candidates: list[str] = Field(default_factory=list)
  requested_fields: list[str] = Field(default_factory=list)
  adjudicate_fields: list[str] = Field(default_factory=list)


class MetadataExtractorAgent(BaseAgent):
  """Small-context metadata fallback. It is never invoked in mock mode."""

  def __init__(self, llm_client) -> None:
    super().__init__("metadata_extractor_agent", llm_client)

  def run(self, agent_input: MetadataExtractionInput) -> ExtractedMetadata:
    if self.is_mock:
      return ExtractedMetadata()
    system = self.prompt_loader.render("metadata_extractor_system.md")
    prompt = self.prompt_loader.render(
      "metadata_extractor_user.md",
      requested_fields=agent_input.requested_fields,
      current_metadata=agent_input.current_metadata.model_dump(
        mode="json", exclude={"source_path", "paper_id", "fields", "candidates"}),
      layout_candidates=[candidate.model_dump(mode="json")
                         for candidate in agent_input.current_metadata.candidates[:80]],
      first_page_text=agent_input.first_page_text[:12000],
      abstract_candidate=(agent_input.abstract_candidate or "")[:5000],
      section_candidates=agent_input.section_candidates[:60],
      adjudicate_fields=agent_input.adjudicate_fields,
      schema_instruction=self.build_schema_instruction(ExtractedMetadata),
    )
    return self.generate_pydantic(prompt, ExtractedMetadata, system_prompt=system,
                                  temperature=0.0, max_tokens=2500)
