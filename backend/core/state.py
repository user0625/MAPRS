from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from backend.schemas.agent_io import (
  AnalysisPlan,
  CriticNotes,
  EvidenceBundle,
  ReaderNotes
)
from backend.schemas.paper import PaperDocument, PaperInput
from backend.schemas.report import FinalReport


class AnalysisStatus(str, Enum):
  """Runtime status of one paper analysis task"""

  CREATED = "created"
  PARSING = "parsing"
  CHUNKING = "chunking"
  RETRIEVING = "retrieving"
  PLANNING = "planning"
  READING = "reading"
  CRITICIZING = "criticizing"
  WRITING = "writing"
  COMPLETED = "completed"
  FAILED = "failed"


class StepStatus(str, Enum):
  """Status of one workflow step"""
  SUCCESS = "success"
  FAILED = "failed"
  SKIPPED = "skipped"

class AnalysisStepRecord(BaseModel):
  """
    record of one executed step.

    this is useful for debugging and future UI display
  """

  step_name:str = Field(..., min_length=1)
  # status:LiteralStatus = Field(default="success")
  status:StepStatus = Field(default=StepStatus.SUCCESS)
  message:str|None = None
  timestamp:datetime = Field(default_factory=datetime.now)
  metadata:dict[str, Any] = Field(default_factory=dict)

  @field_validator("step_name", "message")
  @classmethod
  def strip_optional_text(cls, value:str|None) -> str|None:
    if value is None:
      return None
    value = value.strip()
    return value or None


#helper alias for readability
# LiteralStatus = str


class AnalysisState(BaseModel):
  """
    Global state of one paper analysis workflow.

    Orchestrator should update this object step by step
  """

  task_id:str = Field(..., min_length=1)
  paper_input:PaperInput

  status:AnalysisStatus = AnalysisStatus.CREATED

  document:PaperDocument|None = None
  analysis_plan:AnalysisPlan|None = None
  evidence_bundle:EvidenceBundle|None = None
  reader_notes:ReaderNotes|None = None
  critic_notes:CriticNotes|None = None
  final_report:FinalReport|None = None

  error_message:str|None = None

  created_at:datetime = Field(default_factory=datetime.now)
  updated_at:datetime = Field(default_factory=datetime.now)
  completed_at:datetime|None = None

  step_history:list[AnalysisStepRecord] = Field(default_factory=list)
  metadata:dict[str, Any] = Field(default_factory=dict)

  @field_validator("task_id")
  @classmethod
  def strip_task_id(cls, value:str) -> str:
    value = value.strip()
    if not value:
      raise ValueError("task id cannot be empty")
    return value
  
  def update_status(self, status:AnalysisStatus) -> None:
    """update workflow status and timestamp."""
    self.status = status
    self.updated_at = datetime.now()

    if status == AnalysisStatus.COMPLETED:
      self.completed_at = datetime.now()
  
  def add_step(self, step_name:str, status:StepStatus.SUCCESS, message:str|None=None, metadata:dict[str,Any]|None=None) -> None:
    """append a step execution record."""
    self.step_history.append(
      AnalysisStepRecord(
        step_name=step_name,
        status=status,
        message=message,
        metadata=metadata or {}
      )
    )
    self.updated_at = datetime.now()
  
  def mark_failed(self, message:str) -> str:
    """mark current analysis task as failed"""

    self.status = AnalysisStatus.FAILED
    self.error_message = message.strip()
    self.updated_at = datetime.now()

    self.add_step(step_name="error", status=StepStatus.FAILED, message=self.error_message)

  def mark_completed(self) -> None:
    """Mark current analysis task as completed"""
    self.status = AnalysisStatus.COMPLETED
    now = datetime.now()
    self.updated_at = now
    self.completed_at = now
    self.add_step(step_name="completed", status=StepStatus.SUCCESS, message="Analysis completed successfully")
  
  def is_completed(self) -> bool:
    """return whether the analysis is completed"""
    return self.status == AnalysisStatus.COMPLETED
  
  def is_failed(self) -> bool:
    """return whether the analysis has failed"""
    return self.status == AnalysisStatus.FAILED
  
  def has_document(self) -> bool:
    """return whether document parsing has completed"""
    return self.document is not None
  
  def has_chunks(self) -> bool:
    """return whether the document has chunks"""
    return self.document is not None and self.document.has_chunks()
  
  def has_report(self) -> bool:
    """return whether final report exists."""
    return self.final_report is not None
  
