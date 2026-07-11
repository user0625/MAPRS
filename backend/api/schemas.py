from __future__ import annotations

from pydantic import BaseModel, Field

from datetime import datetime
from typing import Any, Literal


from backend.api.task_store import APITaskStatus


class HealthResponse(BaseModel):
  status: str = "ok"
  service: str = "multi-agent-paper-reader"


class AnalyzeUploadResponse(BaseModel):
  task_id: str
  status: str

  paper_title: str | None = None
  paper_id: str | None = None

  report_markdown: str
  report_path: str | None = None
  state_summary_path: str | None = None

  num_pages: int = 0
  num_chunks: int = 0
  num_evidence_items: int = 0
  num_report_sections: int = 0

  message: str = "Analysis completed successfully."


class ErrorResponse(BaseModel):
  detail: str


class AnalyzeLanguage(str):
  ZH = "zh"
  EN = "en"


OutputLanguage = Literal["zh", "en"]





class TaskCreateResponse(BaseModel):
    task_id: str
    status: APITaskStatus
    message: str


class TaskStatusResponse(BaseModel):
    task_id: str
    status: APITaskStatus
    message: str

    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None

    paper_title: str | None = None
    paper_id: str | None = None

    report_path: str | None = None
    state_json_path: str | None = None

    error_message: str | None = None

    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskReportResponse(BaseModel):
    task_id: str
    status: APITaskStatus
    report_markdown: str
    report_path: str | None = None


class WorkflowStepSummary(BaseModel):
    step_name: str
    status: str
    timestamp: datetime | None = None
    message: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskListResponse(BaseModel):
    items: list[TaskStatusResponse]
    total: int
    limit: int
    offset: int


class TaskDetailResponse(TaskStatusResponse):
    paper_authors: list[str] = Field(default_factory=list)
    report_markdown: str | None = None
    report_available: bool = False
    state_available: bool = False
    workflow_status: str | None = None
    workflow_created_at: datetime | None = None
    workflow_updated_at: datetime | None = None
    workflow_completed_at: datetime | None = None
    workflow_metadata: dict[str, Any] = Field(default_factory=dict)
    step_history: list[WorkflowStepSummary] | None = None
