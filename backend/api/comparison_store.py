from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from sqlalchemy import Column, UniqueConstraint, delete
from sqlmodel import Field, Session, SQLModel, func, select

from backend.api.task_store import JSON_TYPE, utcnow


class ComparisonStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


COMPARISON_TERMINAL = (
    ComparisonStatus.COMPLETED,
    ComparisonStatus.FAILED,
    ComparisonStatus.CANCELED,
)


class PaperComparison(SQLModel, table=True):
    __tablename__ = "paper_comparisons"

    id: str = Field(primary_key=True)
    title: str
    focus: str
    language: str = "zh"
    status: ComparisonStatus = Field(default=ComparisonStatus.PENDING, index=True)
    progress: int = 0
    current_step: str | None = None
    message: str = "Comparison queued."
    error_message: str | None = None
    report_path: str | None = None
    structured_path: str | None = None
    artifacts: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON_TYPE))
    retry_of: str | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    last_event_id: int = 0


class ComparisonPaper(SQLModel, table=True):
    __tablename__ = "comparison_papers"
    __table_args__ = (
        UniqueConstraint("comparison_id", "position"),
        UniqueConstraint("comparison_id", "source_task_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    comparison_id: str = Field(index=True)
    position: int
    source_task_id: str = Field(index=True)
    paper_id: str | None = None
    title: str
    authors: list[str] = Field(default_factory=list, sa_column=Column(JSON_TYPE))
    year: int | None = None
    state_json_path: str


class ComparisonEvidence(SQLModel, table=True):
    __tablename__ = "comparison_evidence"

    evidence_id: str = Field(primary_key=True)
    comparison_id: str = Field(index=True)
    source_task_id: str = Field(index=True)
    paper_id: str | None = None
    paper_title: str
    chunk_id: str
    page_start: int | None = None
    page_end: int | None = None
    section: str | None = None
    text: str
    score: float | None = None
    created_at: datetime = Field(default_factory=utcnow)


class ComparisonEvent(SQLModel, table=True):
    __tablename__ = "comparison_events"
    __table_args__ = (UniqueConstraint("comparison_id", "sequence"),)

    id: int | None = Field(default=None, primary_key=True)
    comparison_id: str = Field(index=True)
    sequence: int
    event_type: str = Field(index=True)
    status: str | None = None
    step: str | None = None
    message: str | None = None
    event_metadata: dict[str, Any] = Field(default_factory=dict, sa_column=Column("metadata", JSON_TYPE))
    created_at: datetime = Field(default_factory=utcnow, index=True)


class ComparisonStore:
    def __init__(self, task_store) -> None:
        self.task_store = task_store
        self.engine = task_store.engine

    def create(
        self,
        comparison_id: str,
        title: str,
        focus: str,
        language: str,
        papers: list[dict[str, Any]],
        retry_of: str | None = None,
    ) -> PaperComparison:
        record = PaperComparison(
            id=comparison_id, title=title, focus=focus, language=language, retry_of=retry_of
        )
        with Session(self.engine) as session:
            session.add(record)
            for position, paper in enumerate(papers):
                session.add(ComparisonPaper(
                    comparison_id=comparison_id,
                    position=position,
                    source_task_id=paper["source_task_id"],
                    paper_id=paper.get("paper_id"),
                    title=paper["title"],
                    authors=list(paper.get("authors") or []),
                    year=paper.get("year"),
                    state_json_path=paper["state_json_path"],
                ))
            session.commit()
        self.add_event(comparison_id, "queued", ComparisonStatus.PENDING, message="Comparison queued.")
        return self.get(comparison_id) or record

    def get(self, comparison_id: str) -> PaperComparison | None:
        with Session(self.engine) as session:
            return session.get(PaperComparison, comparison_id)

    def papers(self, comparison_id: str) -> list[ComparisonPaper]:
        with Session(self.engine) as session:
            return list(session.exec(select(ComparisonPaper).where(ComparisonPaper.comparison_id == comparison_id).order_by(ComparisonPaper.position)).all())

    def list(self, limit: int = 20, offset: int = 0, status: str | None = None, search: str | None = None) -> tuple[list[PaperComparison], int]:
        filters = []
        if status:
            filters.append(PaperComparison.status == status)
        if search:
            pattern = f"%{search}%"
            filters.append((PaperComparison.title.ilike(pattern)) | (PaperComparison.focus.ilike(pattern)))
        with Session(self.engine) as session:
            total = session.exec(select(func.count()).select_from(PaperComparison).where(*filters)).one()
            rows = session.exec(select(PaperComparison).where(*filters).order_by(PaperComparison.created_at.desc(), PaperComparison.id.desc()).offset(offset).limit(limit)).all()
            return list(rows), total

    def update_title(self, comparison_id: str, title: str) -> PaperComparison | None:
        with Session(self.engine) as session:
            record = session.get(PaperComparison, comparison_id)
            if record:
                record.title = title
                record.updated_at = utcnow()
                session.add(record)
                session.commit()
                session.refresh(record)
            return record

    def claim(self, comparison_id: str) -> bool:
        with Session(self.engine) as session:
            record = session.get(PaperComparison, comparison_id)
            if not record or record.status != ComparisonStatus.PENDING:
                return False
            record.status = ComparisonStatus.RUNNING
            record.started_at = record.started_at or utcnow()
            record.updated_at = utcnow()
            record.message = "Comparison is running."
            session.add(record)
            session.commit()
        self.add_event(comparison_id, "started", ComparisonStatus.RUNNING, message="Comparison started.")
        return True

    def progress(self, comparison_id: str, step: str, progress: int, message: str) -> None:
        self._update(comparison_id, current_step=step, progress=progress, message=message)
        self.add_event(comparison_id, "progress", ComparisonStatus.RUNNING, step, message, {"progress": progress})

    def save_evidence(self, items: list[ComparisonEvidence]) -> None:
        if not items:
            return
        comparison_id = items[0].comparison_id
        with Session(self.engine, expire_on_commit=False) as session:
            session.exec(delete(ComparisonEvidence).where(ComparisonEvidence.comparison_id == comparison_id))
            session.add_all(items)
            session.commit()

    def evidence(self, comparison_id: str, evidence_id: str) -> ComparisonEvidence | None:
        with Session(self.engine) as session:
            item = session.get(ComparisonEvidence, evidence_id)
            return item if item and item.comparison_id == comparison_id else None

    def list_evidence(self, comparison_id: str) -> list[ComparisonEvidence]:
        with Session(self.engine) as session:
            return list(session.exec(select(ComparisonEvidence).where(ComparisonEvidence.comparison_id == comparison_id).order_by(ComparisonEvidence.evidence_id)).all())

    def complete(self, comparison_id: str, report_path: str, structured_path: str, artifacts: dict[str, str]) -> None:
        self._update(comparison_id, status=ComparisonStatus.COMPLETED, progress=100, current_step="export", message="Comparison completed.", report_path=report_path, structured_path=structured_path, artifacts=artifacts, completed_at=utcnow())
        self.add_event(comparison_id, "completed", ComparisonStatus.COMPLETED, "export", "Comparison completed.", {"progress": 100})

    def fail(self, comparison_id: str, error: str) -> None:
        self._update(comparison_id, status=ComparisonStatus.FAILED, error_message=error, message="Comparison failed.", completed_at=utcnow())
        self.add_event(comparison_id, "failed", ComparisonStatus.FAILED, message="Comparison failed.")

    def cancel(self, comparison_id: str) -> PaperComparison | None:
        record = self.get(comparison_id)
        if not record:
            return None
        if record.status in (ComparisonStatus.PENDING, ComparisonStatus.RUNNING):
            now = utcnow()
            self._update(comparison_id, cancel_requested_at=now, status=ComparisonStatus.CANCELED, message="Comparison canceled.", completed_at=now)
            self.add_event(comparison_id, "canceled", ComparisonStatus.CANCELED, message="Comparison canceled.")
        return self.get(comparison_id)

    def is_cancel_requested(self, comparison_id: str) -> bool:
        record = self.get(comparison_id)
        return bool(record and (record.cancel_requested_at or record.status == ComparisonStatus.CANCELED))

    def add_event(self, comparison_id: str, event_type: str, status: ComparisonStatus | str | None = None, step: str | None = None, message: str | None = None, metadata: dict[str, Any] | None = None) -> ComparisonEvent:
        with Session(self.engine) as session:
            record = session.get(PaperComparison, comparison_id)
            sequence = (record.last_event_id if record else 0) + 1
            event = ComparisonEvent(comparison_id=comparison_id, sequence=sequence, event_type=event_type, status=status.value if isinstance(status, ComparisonStatus) else status, step=step, message=message, event_metadata=metadata or {})
            session.add(event)
            if record:
                record.last_event_id = sequence
                record.updated_at = utcnow()
                session.add(record)
            session.commit()
            session.refresh(event)
            return event

    def events(self, comparison_id: str, after: int = 0) -> list[ComparisonEvent]:
        with Session(self.engine) as session:
            return list(session.exec(select(ComparisonEvent).where(ComparisonEvent.comparison_id == comparison_id, ComparisonEvent.sequence > after).order_by(ComparisonEvent.sequence).limit(1000)).all())

    def active_for_task(self, task_id: str) -> list[str]:
        with Session(self.engine) as session:
            return list(session.exec(select(PaperComparison.id).join(ComparisonPaper, ComparisonPaper.comparison_id == PaperComparison.id).where(ComparisonPaper.source_task_id == task_id, PaperComparison.status.in_([ComparisonStatus.PENDING, ComparisonStatus.RUNNING]))).all())

    def delete(self, comparison_id: str) -> bool:
        record = self.get(comparison_id)
        if not record or record.status not in COMPARISON_TERMINAL:
            return False
        for raw in record.artifacts.values():
            Path(raw).unlink(missing_ok=True)
        with Session(self.engine) as session:
            for model in (ComparisonEvent, ComparisonEvidence, ComparisonPaper):
                session.exec(delete(model).where(model.comparison_id == comparison_id))
            session.exec(delete(PaperComparison).where(PaperComparison.id == comparison_id))
            session.commit()
        return True

    def _update(self, comparison_id: str, **values: Any) -> None:
        with Session(self.engine) as session:
            record = session.get(PaperComparison, comparison_id)
            if not record:
                return
            for key, value in values.items():
                setattr(record, key, value)
            record.updated_at = utcnow()
            session.add(record)
            session.commit()


def comparison_store_for(task_store) -> ComparisonStore:
    return ComparisonStore(task_store)
