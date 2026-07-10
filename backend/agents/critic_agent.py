from __future__ import annotations

from backend.agents.base_agent import BaseAgent
from backend.schemas.agent_io import CriticInput, CriticNotes, EvidenceBundle, ReaderNotes


class CriticAgent(BaseAgent):
  """
    Critic Agent analyzes strengths, limitations, novelty, reliability,
    missing experiments, and reproducibility risks.

    It should be critical but evidence-grounded.
  """

  def __init__(self, llm_client) -> None:
    super().__init__(
      name="critic_agent",
      llm_client=llm_client,
    )

  def run(self, agent_input: CriticInput) -> CriticNotes:
    if self.is_mock:
      return self._run_mock(agent_input)

    prompt = self._build_prompt(agent_input)
    system_prompt = self._build_system_prompt()

    return self.generate_pydantic(
      prompt=prompt,
      output_schema=CriticNotes,
      system_prompt=system_prompt,
      temperature=0.1,
    )

  def _run_mock(self, agent_input: CriticInput) -> CriticNotes:
    evidence_ids: list[str] = []

    if agent_input.evidence_bundle is not None:
      evidence_ids = agent_input.evidence_bundle.evidence_ids()[:5]

    return CriticNotes(
      strengths=[
        "The paper presents a clear research direction and a structured method.",
        "The proposed approach appears to address a meaningful problem.",
        "The paper provides experimental evidence to support its claims.",
      ],
      limitations=[
        "The scope of evaluation may be limited depending on the datasets and baselines used.",
        "The method may require further validation under more diverse settings.",
      ],
      missing_experiments=[
        "Additional ablation studies could better isolate the contribution of each module.",
        "More comparisons with strong recent baselines would strengthen the empirical analysis.",
      ],
      potential_weaknesses=[
        "The reported improvements may depend on implementation details or dataset-specific factors.",
        "The generalizability of the method may require further investigation.",
      ],
      novelty_assessment=(
        "The work appears moderately novel based on the provided reader notes, "
        "but its novelty should be judged against closely related prior work."
      ),
      reliability_assessment=(
        "The reliability of the conclusions depends on the completeness of the experimental "
        "setup, baseline selection, ablation studies, and reproducibility details."
      ),
      reproducibility_notes=[
        "Check whether the paper provides source code, hyperparameters, and data preprocessing details.",
        "Check whether random seeds, evaluation metrics, and train/validation/test splits are clearly specified.",
      ],
      evidence_ids=evidence_ids,
    )

  def _build_system_prompt(self) -> str:
    return self.prompt_loader.render("critic_system.md")
    # return (
    #   "You are a rigorous scientific paper critic and reviewer. "
    #   "Your job is to analyze a paper's strengths, limitations, missing experiments, "
    #   "novelty, reliability, and reproducibility risks. "
    #   "Be critical but fair. Do not invent unsupported weaknesses. "
    #   "Base your critique only on the provided reader notes and evidence."
    # )


  def _build_prompt(self, agent_input: CriticInput) -> str:
    metadata = agent_input.paper_metadata

    reader_context = self._format_reader_notes(agent_input.reader_notes)
    evidence_context = self._format_evidence_context(agent_input.evidence_bundle)
    schema_instruction = self.build_schema_instruction(CriticNotes)

    return self.prompt_loader.render(
      "critic_user.md",
      title=metadata.title or "Unknown",
      authors=", ".join(metadata.authors) if metadata.authors else "Unknown",
      year=metadata.year or "Unknown",
      venue=metadata.venue or "Unknown",
      abstract=metadata.abstract or "Unknown",
      reader_notes=reader_context,
      evidence_context=evidence_context,
      schema_instruction=schema_instruction,
    )
#   def _build_prompt(self, agent_input: CriticInput) -> str:
#     metadata = agent_input.paper_metadata
#     reader_notes = agent_input.reader_notes

#     reader_context = self._format_reader_notes(reader_notes)
#     evidence_context = self._format_evidence_context(agent_input.evidence_bundle)

#     schema_instruction = self.build_schema_instruction(CriticNotes)

#     return f"""
# Please critically analyze the following paper based on the provided reader notes and evidence.

# Paper metadata:
# - Title: {metadata.title or "Unknown"}
# - Authors: {", ".join(metadata.authors) if metadata.authors else "Unknown"}
# - Year: {metadata.year or "Unknown"}
# - Venue: {metadata.venue or "Unknown"}
# - Abstract: {metadata.abstract or "Unknown"}

# Reader notes:
# {reader_context}

# Retrieved evidence:
# {evidence_context}

# Critique requirements:
# 1. Identify the paper's main strengths.
# 2. Identify limitations and potential weaknesses.
# 3. Point out missing experiments, baselines, ablation studies, or evaluation details.
# 4. Assess novelty carefully and avoid overstating claims.
# 5. Assess reliability based on experimental and methodological evidence.
# 6. Identify reproducibility concerns.
# 7. Use evidence_ids only when the evidence is directly relevant.
# 8. Do not invent details that are not supported by the provided notes or evidence.

# {schema_instruction}
# """.strip()

  def _format_reader_notes(self, reader_notes: ReaderNotes) -> str:
    contributions = "\n".join(
      f"- {item}" for item in reader_notes.main_contributions
    ) or "None provided."

    key_terms = ", ".join(reader_notes.key_terms) or "None provided."

    important_evidence_ids = (
      ", ".join(reader_notes.important_evidence_ids)
      if reader_notes.important_evidence_ids
      else "None provided."
    )

    return f"""
Problem statement:
{reader_notes.problem_statement or "None provided."}

Background:
{reader_notes.background or "None provided."}

Main contributions:
{contributions}

Method summary:
{reader_notes.method_summary or "None provided."}

Experiment summary:
{reader_notes.experiment_summary or "None provided."}

Conclusion summary:
{reader_notes.conclusion_summary or "None provided."}

Key terms:
{key_terms}

Important evidence IDs:
{important_evidence_ids}
""".strip()

  def _format_evidence_context(self, evidence_bundle: EvidenceBundle | None,) -> str:
    if evidence_bundle is None or not evidence_bundle.items:
      return "No retrieved evidence provided."

    blocks: list[str] = []

    for item in evidence_bundle.items:
      location = self._format_location(
        page_start=item.page_start,
        page_end=item.page_end,
        section=item.section,
      )

      blocks.append(
        f"[Evidence ID: {item.evidence_id}]\n"
        f"[Query: {item.query or 'Unknown'}]\n"
        f"[Chunk ID: {item.chunk_id}]\n"
        f"[Score: {item.score if item.score is not None else 'Unknown'}]\n"
        f"[Location: {location}]\n"
        f"{item.text}"
      )

    return "\n\n---\n\n".join(blocks)

  def _format_location(self, page_start: int | None, page_end: int | None, section: str | None,) -> str:
    page_text = "unknown page"

    if page_start is not None and page_end is not None:
      if page_start == page_end:
        page_text = f"page {page_start}"
      else:
        page_text = f"pages {page_start}-{page_end}"
    elif page_start is not None:
      page_text = f"page {page_start}"

    section_text = section or "unknown section"

    return f"{page_text}, {section_text}"