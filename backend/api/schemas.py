from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

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
    code: str | None = None
    request_id: str | None = None


class AnalyzeLanguage(str):
    ZH = "zh"
    EN = "en"


OutputLanguage = Literal["zh", "en"]


class TaskCreateResponse(BaseModel):
    task_id: str
    status: APITaskStatus
    message: str
    deduplicated: bool = False


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
    progress: int = 0
    current_step: str | None = None
    attempt_count: int = 0
    last_checkpoint_step: str | None = None
    last_event_id: int = 0

    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskReportResponse(BaseModel):
    task_id: str
    status: APITaskStatus
    report_markdown: str
    report_path: str | None = None


class StructuredClaimResponse(BaseModel):
    text: str
    evidence_ids: list[str] = Field(default_factory=list)


class StructuredSectionResponse(BaseModel):
    title: str
    content: str = ""
    order: int = 0
    evidence_ids: list[str] = Field(default_factory=list)
    claims: list[StructuredClaimResponse] = Field(default_factory=list)


class StructuredReportBodyResponse(BaseModel):
    title: str = "Paper Reading Report"
    paper_title: str | None = None
    sections: list[StructuredSectionResponse] = Field(default_factory=list)
    quality_summary: dict[str, Any] | None = None


class EvidenceIndexItemResponse(BaseModel):
    evidence_id: str
    chunk_id: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    section: str | None = None


class StructuredReportResponse(BaseModel):
    task_id: str
    report: StructuredReportBodyResponse
    quality_summary: dict[str, Any] = Field(default_factory=dict)
    evidence_index: list[EvidenceIndexItemResponse] = Field(default_factory=list)


class EvidenceResponse(EvidenceIndexItemResponse):
    task_id: str
    text: str = Field(max_length=4000)


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


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    language: Literal["auto", "zh", "en"] = "auto"


class ConversationUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ConversationResponse(BaseModel):
    id: str
    task_id: str
    title: str
    language: str
    created_at: datetime
    updated_at: datetime


class ConversationListResponse(BaseModel):
    items: list[ConversationResponse]


class AskMessageResponse(BaseModel):
    id: str
    conversation_id: str
    role: str
    content: str
    status: str
    language: str
    section: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    citation_ids: list[str] = Field(default_factory=list)
    error: str | None = None
    retry_of: str | None = None
    created_at: datetime
    updated_at: datetime


class ConversationDetailResponse(ConversationResponse):
    messages: list[AskMessageResponse]
    total: int
    limit: int
    offset: int


class AskMessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=8000)
    section: str | None = Field(default=None, max_length=300)
    page_start: int | None = Field(default=None, ge=1, le=100000)
    page_end: int | None = Field(default=None, ge=1, le=100000)
    language: Literal["auto", "zh", "en"] = "auto"

    @model_validator(mode="after")
    def validate_page_range(self):
        if (self.page_start is None) != (self.page_end is None):
            raise ValueError("page_start and page_end must be provided together")
        if (
            self.page_start is not None
            and self.page_end is not None
            and self.page_start > self.page_end
        ):
            raise ValueError("page_start must not exceed page_end")
        return self


class AskAcceptedResponse(BaseModel):
    user_message_id: str | None
    assistant_message_id: str
    status: str
