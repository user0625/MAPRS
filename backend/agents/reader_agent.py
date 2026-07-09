from __future__ import annotations

from backend.agents.base_agent import BaseAgent
from backend.schemas.agent_io import EvidenceBundle, ReaderInput, ReaderNotes
from backend.schemas.paper import PaperChunk


class ReaderAgent(BaseAgent):
  """
    Reader Agent extracts faithful and structured notes from a paper.

    It should summarize what the paper says, not criticize it.
  """

  def __init__(self, llm_client) -> None:
    super().__init__(
      name="reader_agent",
      llm_client=llm_client,
    )

  def run(self, agent_input: ReaderInput) -> ReaderNotes:
    if self.is_mock:
      return self._run_mock(agent_input)

    prompt = self._build_prompt(agent_input)
    system_prompt = self._build_system_prompt()

    return self.generate_pydantic(
      prompt=prompt,
      output_schema=ReaderNotes,
      system_prompt=system_prompt,
      temperature=0.1,
    )

  def _run_mock(self, agent_input: ReaderInput) -> ReaderNotes:
    """
    Return deterministic mock reader notes for tests and local development.
    """

    title = agent_input.paper_metadata.title or "Unknown Paper"

    evidence_ids: list[str] = []
    if agent_input.evidence_bundle is not None:
        evidence_ids = agent_input.evidence_bundle.evidence_ids()[:5]

    return ReaderNotes(
      problem_statement=(
        f"The paper '{title}' addresses a scientific research problem "
        "and proposes a method to solve it."
      ),
      background=(
        "The paper is motivated by limitations in existing approaches "
        "and aims to improve research understanding or task performance."
      ),
      main_contributions=[
        "The paper proposes a structured method or system.",
        "The paper evaluates the proposed approach through experiments.",
        "The paper discusses results and potential implications.",
      ],
      method_summary=(
        "The method is summarized as a pipeline consisting of input processing, "
        "core modeling or reasoning modules, and final output generation."
      ),
      experiment_summary=(
        "The experiments are designed to evaluate the effectiveness of the proposed method."
      ),
      conclusion_summary=(
          "The paper concludes that the proposed method is useful for the target task."
      ),
      key_terms=[
        "paper understanding",
        "method",
        "experiment",
        "evaluation",
      ],
      important_evidence_ids=evidence_ids,
    )

  def _build_system_prompt(self) -> str:
    return (
      "You are a careful scientific paper reader. "
      "Your job is to faithfully extract and summarize the paper's content. "
      "Do not criticize the paper. Do not invent facts. "
      "Only summarize what can be supported by the provided paper text."
    )

  def _build_prompt(self, agent_input: ReaderInput) -> str:
      metadata = agent_input.paper_metadata
      plan = agent_input.analysis_plan

      context = self._build_context(agent_input)
      focus_questions = "\n".join(
        f"- {question}" for question in plan.focus_questions
      )

      tasks = "\n".join(
        f"- {task.name}: {task.description}" for task in plan.tasks
      )
      schema_instruction = self.build_schema_instruction(ReaderNotes)
      return f"""
Please read the following paper content and extract structured reader notes.

Paper metadata:
- Title: {metadata.title or "Unknown"}
- Authors: {", ".join(metadata.authors) if metadata.authors else "Unknown"}
- Year: {metadata.year or "Unknown"}
- Venue: {metadata.venue or "Unknown"}
- Abstract: {metadata.abstract or "Unknown"}

Analysis tasks:
{tasks or "No explicit tasks provided."}

Focus questions:
{focus_questions or "No explicit focus questions provided."}

Paper context:
{context}

Instructions:
1. Faithfully summarize the paper content.
2. Extract the research problem, background, main contributions, method, experiments, and conclusion.
3. Do not criticize the paper in this step.
4. Do not mention information that is not supported by the provided context.
5. If evidence IDs are provided, include the most relevant ones in important_evidence_ids.

{schema_instruction}
""".strip()

  def _build_context(self, agent_input: ReaderInput) -> str:
    """
    Build textual context for the Reader Agent.
    Priority:
    1. Use retrieved evidence if available.
    2. Otherwise use the first several chunks.
    """
    if agent_input.evidence_bundle is not None and agent_input.evidence_bundle.items:
      return self._format_evidence_context(agent_input.evidence_bundle)

    return self._format_chunk_context(agent_input.chunks)

  def _format_evidence_context(self, evidence_bundle: EvidenceBundle) -> str:
    blocks: list[str] = []

    for item in evidence_bundle.items:
      location = self._format_location(
        page_start=item.page_start,
        page_end=item.page_end,
        section=item.section,
      )
      blocks.append(
        f"[Evidence ID: {item.evidence_id}]\n"
        f"[Chunk ID: {item.chunk_id}]\n"
        f"[Location: {location}]\n"
        f"{item.text}"
      )
    return "\n\n---\n\n".join(blocks)

  def _format_chunk_context(self, chunks: list[PaperChunk], max_chunks: int = 12) -> str:
    selected_chunks = chunks[:max_chunks]

    blocks: list[str] = []

    for chunk in selected_chunks:
      location = self._format_location(
        page_start=chunk.page_start,
        page_end=chunk.page_end,
        section=chunk.section,
      )

      blocks.append(
        f"[Chunk ID: {chunk.chunk_id}]\n"
        f"[Location: {location}]\n"
        f"{chunk.text}"
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