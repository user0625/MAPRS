from __future__ import annotations

from backend.agents.base_agent import BaseAgent
from backend.schemas.agent_io import (
    CriticNotes,
    EvidenceBundle,
    ReaderNotes,
    WriterInput,
)
from backend.schemas.report import FinalReport, ReportFormat, ReportSection


class WriterAgent(BaseAgent):
  """
    Writer Agent generates the final structured paper reading report.

    It integrates Planner, Reader, Critic, and Evidence outputs into a FinalReport.
  """

  def __init__(self, llm_client) -> None:
    super().__init__(
      name="writer_agent",
      llm_client=llm_client,
    )

  def run(self, agent_input: WriterInput) -> FinalReport:
    if self.is_mock:
      return self._run_mock(agent_input)

    prompt = self._build_prompt(agent_input)
    system_prompt = self._build_system_prompt(agent_input.output_language)

    return self.generate_pydantic(
      prompt=prompt,
      output_schema=FinalReport,
      system_prompt=system_prompt,
      temperature=0.1,
    )

  def revise(self, agent_input: WriterInput, report: FinalReport,
             instructions: list[str]) -> FinalReport:
    """Perform the single quality-gate revision without rereading the PDF."""
    if self.is_mock or not instructions:
      return report
    allowed_ids = agent_input.evidence_bundle.evidence_ids() if agent_input.evidence_bundle else []
    prompt = self.prompt_loader.render("writer_revision_user.md",
      report=report.model_dump(mode="json"), instructions=instructions,
      allowed_evidence_ids=allowed_ids,
      schema_instruction=self.build_schema_instruction(FinalReport))
    system = self._build_system_prompt(agent_input.output_language)
    return self.generate_pydantic(prompt, FinalReport, system_prompt=system,
                                  temperature=0.0, max_tokens=5000)

  def _run_mock(self, agent_input: WriterInput) -> FinalReport:
    """
    Return deterministic mock report for tests and local development.
    """

    metadata = agent_input.paper_metadata
    reader_notes = agent_input.reader_notes
    critic_notes = agent_input.critic_notes

    paper_title = metadata.title or "Unknown Paper"

    if agent_input.output_language == "zh":
      sections = self._build_mock_sections_zh(
        reader_notes=reader_notes,
        critic_notes=critic_notes,
      )
      title = "论文阅读报告"
    else:
      sections = self._build_mock_sections_en(
        reader_notes=reader_notes,
        critic_notes=critic_notes,
      )
      title = "Paper Reading Report"

    if agent_input.analysis_depth == "quick":
      sections = sections[:4]
    if agent_input.report_template == "review":
      keep = ("TL;DR", "贡献", "局限", "strength", "limitation", "contribution")
      selected = [section for section in sections if any(word.lower() in section.title.lower() for word in keep)]
      sections = selected or sections
    elif agent_input.report_template == "reproducibility":
      keep = ("方法", "实验", "复现", "method", "experiment", "reproduc")
      selected = [section for section in sections if any(word.lower() in section.title.lower() for word in keep)]
      sections = selected or sections
    for title_text in agent_input.custom_sections:
      sections.append(ReportSection(title=title_text,
        content="该自定义章节需结合所提供证据进一步核对。" if agent_input.output_language == "zh"
        else "This custom section should be checked against the cited evidence.",
        order=len(sections) + 1))

    report = FinalReport(
      title=title,
      paper_title=paper_title,
      sections=sections,
      format=ReportFormat.MARKDOWN,
      metadata={
        "paper_id": metadata.paper_id,
        "output_language": agent_input.output_language,
        "generated_by": self.name,
        "mode": agent_input.analysis_plan.mode,
        "analysis_depth": agent_input.analysis_depth,
        "target_audience": agent_input.target_audience,
        "report_template": agent_input.report_template,
      },
    )

    report.markdown_content = report.to_markdown()

    return report

  def _build_mock_sections_zh(self, reader_notes: ReaderNotes, critic_notes: CriticNotes,) -> list[ReportSection]:
    return [
      ReportSection(
        title="基本信息",
        content="本报告由多智能体论文阅读系统自动生成。",
        order=1,
      ),
      ReportSection(
        title="TL;DR",
        content=self._build_tldr_zh(reader_notes, critic_notes),
        order=2,
      ),
      ReportSection(
        title="研究问题",
        content=reader_notes.problem_statement or "暂无明确研究问题总结。",
        order=3,
        evidence_ids=reader_notes.important_evidence_ids,
      ),
      ReportSection(
        title="主要贡献",
        content=self._format_bullets(reader_notes.main_contributions),
        order=4,
        evidence_ids=reader_notes.important_evidence_ids,
      ),
      ReportSection(
        title="方法总结",
        content=reader_notes.method_summary or "暂无方法总结。",
        order=5,
        evidence_ids=reader_notes.important_evidence_ids,
      ),
      ReportSection(
        title="实验与结果",
        content=reader_notes.experiment_summary or "暂无实验总结。",
        order=6,
        evidence_ids=reader_notes.important_evidence_ids,
      ),
      ReportSection(
        title="优点",
        content=self._format_bullets(critic_notes.strengths),
        order=7,
        evidence_ids=critic_notes.evidence_ids,
      ),
      ReportSection(
        title="局限性与潜在问题",
        content=self._build_limitations_section_zh(critic_notes),
        order=8,
        evidence_ids=critic_notes.evidence_ids,
      ),
      ReportSection(
        title="可复现性注意事项",
        content=self._format_bullets(critic_notes.reproducibility_notes),
        order=9,
        evidence_ids=critic_notes.evidence_ids,
      ),
      ReportSection(
        title="综合评价",
        content=(
            f"创新性评价：{critic_notes.novelty_assessment}\n\n"
            f"可靠性评价：{critic_notes.reliability_assessment}"
        ),
        order=10,
        evidence_ids=critic_notes.evidence_ids,
      ),
    ]

  def _build_mock_sections_en(self, reader_notes: ReaderNotes, critic_notes: CriticNotes,) -> list[ReportSection]:
    return [
      ReportSection(
        title="Basic Information",
        content="This report is generated by the Multi-Agent Paper Reader System.",
        order=1,
      ),
      ReportSection(
        title="TL;DR",
        content=self._build_tldr_en(reader_notes, critic_notes),
        order=2,
      ),
      ReportSection(
        title="Problem Statement",
        content=reader_notes.problem_statement or "No clear problem statement available.",
        order=3,
        evidence_ids=reader_notes.important_evidence_ids,
      ),
      ReportSection(
        title="Main Contributions",
        content=self._format_bullets(reader_notes.main_contributions),
        order=4,
        evidence_ids=reader_notes.important_evidence_ids,
      ),
      ReportSection(
        title="Method Summary",
        content=reader_notes.method_summary or "No method summary available.",
        order=5,
        evidence_ids=reader_notes.important_evidence_ids,
      ),
      ReportSection(
        title="Experimental Setup and Results",
        content=reader_notes.experiment_summary or "No experiment summary available.",
        order=6,
        evidence_ids=reader_notes.important_evidence_ids,
      ),
      ReportSection(
        title="Strengths",
        content=self._format_bullets(critic_notes.strengths),
        order=7,
        evidence_ids=critic_notes.evidence_ids,
      ),
      ReportSection(
        title="Limitations and Weaknesses",
        content=self._build_limitations_section_en(critic_notes),
        order=8,
        evidence_ids=critic_notes.evidence_ids,
      ),
      ReportSection(
        title="Reproducibility Notes",
        content=self._format_bullets(critic_notes.reproducibility_notes),
        order=9,
        evidence_ids=critic_notes.evidence_ids,
      ),
      ReportSection(
        title="Overall Assessment",
        content=(
          f"Novelty assessment: {critic_notes.novelty_assessment}\n\n"
          f"Reliability assessment: {critic_notes.reliability_assessment}"
        ),
        order=10,
        evidence_ids=critic_notes.evidence_ids,
      ),
    ]

  def _build_system_prompt(self, output_language: str) -> str:
    language_instruction = (
      "Write the final report in Chinese."
      if output_language == "zh"
      else "Write the final report in English."
    )
    return self.prompt_loader.render("writer_system.md", language_instruction=language_instruction)
    # return (
    #   "You are a professional scientific writing assistant. "
    #   "Your job is to generate a structured paper reading report based on "
    #   "the provided planner, reader, critic, and evidence outputs. "
    #   "Do not invent unsupported details. "
    #   "Keep the report clear, faithful, and useful for research review. "
    #   f"{language_instruction}"
    # )


  def _build_prompt(self, agent_input: WriterInput) -> str:
    metadata = agent_input.paper_metadata

    plan_context = self._format_analysis_plan(agent_input)
    reader_context = self._format_reader_notes(agent_input.reader_notes)
    critic_context = self._format_critic_notes(agent_input.critic_notes)
    evidence_context = self._format_evidence_context(agent_input.evidence_bundle)

    schema_instruction = self.build_schema_instruction(FinalReport)

    return self.prompt_loader.render(
      "writer_user.md",
      title=metadata.title or "Unknown",
      authors=", ".join(metadata.authors) if metadata.authors else "Unknown",
      year=metadata.year or "Unknown",
      venue=metadata.venue or "Unknown",
      abstract=metadata.abstract or "Unknown",
      output_language=agent_input.output_language,
      analysis_plan=plan_context,
      reader_notes=reader_context,
      critic_notes=critic_context,
      evidence_context=evidence_context,
      schema_instruction=schema_instruction,
    )
#   def _build_prompt(self, agent_input: WriterInput) -> str:
#     metadata = agent_input.paper_metadata

#     plan_context = self._format_analysis_plan(agent_input)
#     reader_context = self._format_reader_notes(agent_input.reader_notes)
#     critic_context = self._format_critic_notes(agent_input.critic_notes)
#     evidence_context = self._format_evidence_context(agent_input.evidence_bundle)

#     schema_instruction = self.build_schema_instruction(FinalReport)

#     return f"""
# Please generate a structured final paper reading report.

# Paper metadata:
# - Title: {metadata.title or "Unknown"}
# - Authors: {", ".join(metadata.authors) if metadata.authors else "Unknown"}
# - Year: {metadata.year or "Unknown"}
# - Venue: {metadata.venue or "Unknown"}
# - Abstract: {metadata.abstract or "Unknown"}

# Output language:
# {agent_input.output_language}

# Analysis plan:
# {plan_context}

# Reader notes:
# {reader_context}

# Critic notes:
# {critic_context}

# Retrieved evidence:
# {evidence_context}

# Report requirements:
# 1. Generate a structured paper reading report.
# 2. Include basic information, TL;DR, problem statement, main contributions, method summary, experiments, strengths, limitations, reproducibility notes, and overall assessment.
# 3. Use the reader notes for factual paper content.
# 4. Use the critic notes for evaluation and critique.
# 5. Use evidence IDs only when directly relevant.
# 6. Do not invent unsupported technical details.
# 7. The report should be useful for research review and future paper writing.
# 8. Return a valid JSON object matching the FinalReport schema.
# 9. The sections field must not be empty.

# {schema_instruction}
# """.strip()

  def _format_analysis_plan(self, agent_input: WriterInput) -> str:
    plan = agent_input.analysis_plan

    tasks = "\n".join(
      f"- {task.name}: {task.description}" for task in plan.tasks
    ) or "No tasks provided."

    focus_questions = "\n".join(
      f"- {question}" for question in plan.focus_questions
    ) or "No focus questions provided."

    required_sections = ", ".join(plan.required_sections) or "None provided."

    return f"""
Mode: {plan.mode}

Tasks:
{tasks}

Focus questions:
{focus_questions}

Required sections:
{required_sections}

Need retrieval: {plan.need_retrieval}
""".strip()

  def _format_reader_notes(self, reader_notes: ReaderNotes) -> str:
    return f"""
Problem statement:
{reader_notes.problem_statement or "None provided."}

Background:
{reader_notes.background or "None provided."}

Main contributions:
{self._format_bullets(reader_notes.main_contributions)}

Method summary:
{reader_notes.method_summary or "None provided."}

Experiment summary:
{reader_notes.experiment_summary or "None provided."}

Conclusion summary:
{reader_notes.conclusion_summary or "None provided."}

Key terms:
{", ".join(reader_notes.key_terms) if reader_notes.key_terms else "None provided."}

Important evidence IDs:
{", ".join(reader_notes.important_evidence_ids) if reader_notes.important_evidence_ids else "None provided."}
""".strip()

  def _format_critic_notes(self, critic_notes: CriticNotes) -> str:
    return f"""
Strengths:
{self._format_bullets(critic_notes.strengths)}

Limitations:
{self._format_bullets(critic_notes.limitations)}

Missing experiments:
{self._format_bullets(critic_notes.missing_experiments)}

Potential weaknesses:
{self._format_bullets(critic_notes.potential_weaknesses)}

Novelty assessment:
{critic_notes.novelty_assessment or "None provided."}

Reliability assessment:
{critic_notes.reliability_assessment or "None provided."}

Reproducibility notes:
{self._format_bullets(critic_notes.reproducibility_notes)}

Evidence IDs:
{", ".join(critic_notes.evidence_ids) if critic_notes.evidence_ids else "None provided."}
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

  def _format_location(
      self,
      page_start: int | None,
      page_end: int | None,
      section: str | None,
  ) -> str:
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

  def _format_bullets(self, items: list[str]) -> str:
    if not items:
      return "None provided."

    return "\n".join(f"- {item}" for item in items)

  def _build_tldr_zh(self, reader_notes: ReaderNotes, critic_notes: CriticNotes,) -> str:
    contribution = (
      reader_notes.main_contributions[0]
      if reader_notes.main_contributions
      else "该论文提出了一种用于目标任务的方法或系统。"
    )

    limitation = (
      critic_notes.limitations[0]
      if critic_notes.limitations
      else "仍需进一步验证其泛化性和可靠性。"
    )

    return f"{contribution} 主要局限在于：{limitation}"

  def _build_tldr_en(self, reader_notes: ReaderNotes, critic_notes: CriticNotes,) -> str:
    contribution = (
      reader_notes.main_contributions[0]
      if reader_notes.main_contributions
      else "The paper proposes a method or system for the target task."
    )

    limitation = (
      critic_notes.limitations[0]
      if critic_notes.limitations
      else "Further validation is needed to assess generalizability and reliability."
    )

    return f"{contribution} The main limitation is: {limitation}"

  def _build_limitations_section_zh(self, critic_notes: CriticNotes) -> str:
    parts = []

    if critic_notes.limitations:
      parts.append("主要局限性：\n" + self._format_bullets(critic_notes.limitations))

    if critic_notes.potential_weaknesses:
      parts.append("潜在问题：\n" + self._format_bullets(critic_notes.potential_weaknesses))

    if critic_notes.missing_experiments:
      parts.append("可能缺失的实验：\n" + self._format_bullets(critic_notes.missing_experiments))

    return "\n\n".join(parts) if parts else "暂无明确局限性总结。"

  def _build_limitations_section_en(self, critic_notes: CriticNotes) -> str:
    parts = []

    if critic_notes.limitations:
      parts.append("Main limitations:\n" + self._format_bullets(critic_notes.limitations))

    if critic_notes.potential_weaknesses:
      parts.append("Potential weaknesses:\n" + self._format_bullets(critic_notes.potential_weaknesses))

    if critic_notes.missing_experiments:
      parts.append("Missing experiments:\n" + self._format_bullets(critic_notes.missing_experiments))

    return "\n\n".join(parts) if parts else "No clear limitations available."
