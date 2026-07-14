from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

from datetime import datetime
from typing import Any, Literal


from backend.api.task_store import APITaskStatus
from backend.api.comparison_store import ComparisonStatus


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


class DocumentSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=8000)
    mode: Literal["auto", "bm25"] = "auto"
    section: str | None = Field(default=None, max_length=300)
    page_start: int | None = Field(default=None, ge=1, le=100000)
    page_end: int | None = Field(default=None, ge=1, le=100000)
    top_k: int = Field(default=6, ge=1, le=20)

    @field_validator("query", mode="before")
    @classmethod
    def strip_query(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        value = value.strip()
        if not value:
            raise ValueError("query must not be empty")
        return value

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


class DocumentSearchContext(BaseModel):
    relation: Literal["before", "after"]
    chunk_id: str
    text: str = Field(max_length=600)
    section: str | None = None
    page_start: int | None = None
    page_end: int | None = None


class DocumentSearchHit(BaseModel):
    rank: int
    chunk_id: str
    text: str = Field(max_length=1200)
    section: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    sources: list[Literal["bm25", "vector"]] = Field(default_factory=list)
    bm25_score: float | None = None
    vector_score: float | None = None
    hybrid_score: float | None = None
    context: list[DocumentSearchContext] = Field(default_factory=list)


class DocumentSearchDiagnostics(BaseModel):
    actual_mode: Literal["hybrid", "bm25", "degraded_to_bm25"]
    candidate_count: int = Field(ge=0)
    elapsed_ms: float = Field(ge=0)
    index_source: Literal["memory_hit", "disk_hit", "cold_build", "unavailable"]
    fallback_reason: Literal[
        "index_build_unavailable", "query_embedding_unavailable"
    ] | None = None


class DocumentSearchResponse(BaseModel):
    task_id: str
    query: str
    mode_used: Literal["hybrid", "bm25", "degraded_to_bm25"]
    hits: list[DocumentSearchHit] = Field(default_factory=list)
    diagnostics: DocumentSearchDiagnostics


class ComparisonCreate(BaseModel):
    task_ids: list[str] = Field(min_length=2, max_length=5)
    title: str | None = Field(default=None, max_length=200)
    focus: str = Field(default="方法、实验结果与局限的综合比较", min_length=1, max_length=4000)
    language: OutputLanguage = "zh"

    @model_validator(mode="after")
    def unique_tasks(self):
        if len(self.task_ids) != len(set(self.task_ids)):
            raise ValueError("task_ids must be unique")
        return self


class ComparisonUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ComparisonPaperResponse(BaseModel):
    source_task_id: str
    paper_id: str | None = None
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    position: int


class ComparisonResponse(BaseModel):
    id: str
    title: str
    focus: str
    language: OutputLanguage
    status: ComparisonStatus
    progress: int
    current_step: str | None = None
    message: str
    error_message: str | None = None
    retry_of: str | None = None
    report_available: bool = False
    structured_available: bool = False
    artifact_formats: list[str] = Field(default_factory=list)
    last_event_id: int = 0
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    papers: list[ComparisonPaperResponse] = Field(default_factory=list)


class ComparisonListResponse(BaseModel):
    items: list[ComparisonResponse]
    total: int
    limit: int
    offset: int


class ComparisonReportResponse(BaseModel):
    comparison_id: str
    report_markdown: str
    report_path: str | None = None


class ComparisonEvidenceResponse(BaseModel):
    comparison_id: str
    evidence_id: str
    source_task_id: str
    paper_id: str | None = None
    paper_title: str
    chunk_id: str
    page_start: int | None = None
    page_end: int | None = None
    section: str | None = None
    text: str
    score: float | None = None
