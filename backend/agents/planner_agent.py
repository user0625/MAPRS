from __future__ import annotations

from backend.agents.base_agent import BaseAgent
from backend.schemas.agent_io import (
  AgentRole,
  AgentTask,
  AnalysisPlan,
  PlannerInput,
)


class PlannerAgent(BaseAgent):
  """
   Planner Agent generates a structured analysis plan for a paper.
  """

  def __init__(self, llm_client) -> None:
    super().__init__(
      name="planner_agent",
      llm_client=llm_client,
    )

  def run(self, agent_input: PlannerInput) -> AnalysisPlan:
    if self.is_mock:
      return self._run_mock(agent_input)

    prompt = self._build_prompt(agent_input)
    system_prompt = self._build_system_prompt()

    return self.generate_pydantic(
      prompt=prompt,
      output_schema=AnalysisPlan,
      system_prompt=system_prompt,
      temperature=0.1,
    )

  def _run_mock(self, agent_input: PlannerInput) -> AnalysisPlan:
    """
    Return deterministic mock plan for tests and local development.
    """

    return AnalysisPlan(
      tasks=[
        AgentTask(
          task_id="task_001",
          name="extract_problem_statement",
          description="Extract the research problem and background.",
          assigned_to=AgentRole.READER,
          priority=1,
        ),
        AgentTask(
          task_id="task_002",
          name="extract_main_contributions",
          description="Extract the main contributions of the paper.",
          assigned_to=AgentRole.READER,
          priority=1,
        ),
        AgentTask(
          task_id="task_003",
          name="summarize_method",
          description="Summarize the proposed method and technical pipeline.",
          assigned_to=AgentRole.READER,
          priority=2,
        ),
        AgentTask(
          task_id="task_004",
          name="analyze_experiments",
          description="Summarize the experimental setup and main results.",
          assigned_to=AgentRole.READER,
          priority=2,
        ),
        AgentTask(
          task_id="task_005",
          name="criticize_limitations",
          description="Analyze limitations, missing experiments, and reproducibility risks.",
          assigned_to=AgentRole.CRITIC,
          priority=3,
        ),
        AgentTask(
          task_id="task_006",
          name="write_report",
          description="Generate the final structured paper reading report.",
          assigned_to=AgentRole.WRITER,
          priority=4,
        ),
      ],
      focus_questions=[
          "What problem does the paper solve?",
          "Why is this problem important?",
          "What are the main contributions?",
          "How does the proposed method work?",
          "How is the method evaluated?",
          "What are the strengths and limitations?",
          "What are the reproducibility concerns?",
      ],
      required_sections=[
        "Abstract",
        "Introduction",
        "Method",
        "Experiments",
        "Results",
        "Conclusion",
      ],
      need_retrieval=True,
      notes=(
        "This is a default mock analysis plan for single-paper analysis."
      ),
    )

  def _build_system_prompt(self) -> str:
    return self.prompt_loader.render("planner_system.md")
    # return (
    #   "You are a scientific paper analysis planner. "
    #   "Your job is to create a structured analysis plan for a multi-agent paper reading system. "
    #   "You must produce concise, accurate, and executable planning outputs."
    # )

  def _build_prompt(self, agent_input: PlannerInput) -> str:
    metadata = agent_input.paper_metadata
    schema_instruction = self.build_schema_instruction(AnalysisPlan)

    return self.prompt_loader.render(
      "planner_user.md",
      title=metadata.title or "Unknown",
      authors=", ".join(metadata.authors) if metadata.authors else "Unknown",
      year=metadata.year or "Unknown",
      venue=metadata.venue or "Unknown",
      abstract=metadata.abstract or "Unknown",
      user_query=agent_input.user_query,
      schema_instruction=schema_instruction,
    )
#   def _build_prompt(self, agent_input: PlannerInput) -> str:
#     metadata = agent_input.paper_metadata

#     schema_instruction = self.build_schema_instruction(AnalysisPlan)

#     return f"""
# Please create a structured analysis plan for the following paper.

# Paper metadata:
# - Title: {metadata.title or "Unknown"}
# - Authors: {", ".join(metadata.authors) if metadata.authors else "Unknown"}
# - Year: {metadata.year or "Unknown"}
# - Venue: {metadata.venue or "Unknown"}
# - Abstract: {metadata.abstract or "Unknown"}

# User query:
# {agent_input.user_query}

# The plan should include:
# 1. A list of analysis tasks.
# 2. Focus questions that guide retrieval and reading.
# 3. Required paper sections to inspect.
# 4. Whether retrieval is needed.

# {schema_instruction}
# """.strip()