from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, Session, SQLModel, create_engine, func, select


class APITaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class APITaskRecord(SQLModel, table=True):
    __tablename__ = "api_tasks"

    task_id: str = Field(primary_key=True)
    status: APITaskStatus = Field(default=APITaskStatus.PENDING, index=True)
    message: str = "Task created."
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None
    input_pdf_path: str | None = None
    report_path: str | None = None
    state_json_path: str | None = None
    paper_title: str | None = None
    paper_id: str | None = None
    error_message: str | None = None
    task_metadata: dict[str, Any] = Field(
        default_factory=dict,
        serialization_alias="metadata",
        sa_column=Column("metadata", JSON),
    )


class DatabaseTaskStore:
    """SQLite-backed task store. Every operation owns its database session."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        if database_url.startswith("sqlite:///"):
            database_path = database_url.removeprefix("sqlite:///")
            if database_path != ":memory:":
                Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            database_url,
            connect_args={"check_same_thread": False} if database_url.startswith("sqlite") else {},
        )

    def create_tables(self) -> None:
        SQLModel.metadata.create_all(self.engine)

    def create_task(
        self, task_id: str, input_pdf_path: str, metadata: dict[str, Any] | None = None
    ) -> APITaskRecord:
        record = APITaskRecord(
            task_id=task_id, input_pdf_path=input_pdf_path, task_metadata=metadata or {}
        )
        with Session(self.engine) as session:
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    def get_task(self, task_id: str) -> APITaskRecord | None:
        with Session(self.engine) as session:
            return session.get(APITaskRecord, task_id)

    def list_tasks(self, limit: int = 20, offset: int = 0) -> tuple[list[APITaskRecord], int]:
        with Session(self.engine) as session:
            total = session.exec(select(func.count()).select_from(APITaskRecord)).one()
            statement = (
                select(APITaskRecord)
                .order_by(APITaskRecord.created_at.desc(), APITaskRecord.task_id.desc())
                .offset(offset)
                .limit(limit)
            )
            return list(session.exec(statement).all()), total

    def mark_running(self, task_id: str, message: str = "Task is running.") -> None:
        self._update(task_id, status=APITaskStatus.RUNNING, message=message)

    def mark_completed(
        self,
        task_id: str,
        report_path: str,
        state_json_path: str | None = None,
        paper_title: str | None = None,
        paper_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        message: str = "Task completed successfully.",
    ) -> None:
        self._update(
            task_id,
            status=APITaskStatus.COMPLETED,
            message=message,
            report_path=report_path,
            state_json_path=state_json_path,
            paper_title=paper_title,
            paper_id=paper_id,
            completed_at=datetime.utcnow(),
            metadata_update=metadata,
        )

    def mark_failed(
        self,
        task_id: str,
        error_message: str,
        message: str = "Task failed.",
        state_json_path: str | None = None,
    ) -> None:
        self._update(
            task_id,
            status=APITaskStatus.FAILED,
            message=message,
            error_message=error_message,
            state_json_path=state_json_path,
            completed_at=datetime.utcnow(),
        )

    def recover_interrupted_tasks(self) -> int:
        now = datetime.utcnow()
        with Session(self.engine) as session:
            tasks = session.exec(
                select(APITaskRecord).where(
                    APITaskRecord.status.in_([APITaskStatus.PENDING, APITaskStatus.RUNNING])
                )
            ).all()
            for task in tasks:
                task.status = APITaskStatus.FAILED
                task.message = "Task interrupted by service restart."
                task.error_message = "The service restarted before the task completed."
                task.updated_at = now
                task.completed_at = now
                session.add(task)
            session.commit()
            return len(tasks)

    def _update(self, task_id: str, metadata_update: dict[str, Any] | None = None, **updates: Any) -> None:
        with Session(self.engine) as session:
            record = session.get(APITaskRecord, task_id)
            if record is None:
                return
            for key, value in updates.items():
                setattr(record, key, value)
            if metadata_update:
                record.task_metadata = {**record.task_metadata, **metadata_update}
            record.updated_at = datetime.utcnow()
            session.add(record)
            session.commit()


# Reconfigured during app creation from AppSettings; kept as a module-level dependency
# so routes and background jobs share the same persistent store.
task_store = DatabaseTaskStore("sqlite:///backend/data/tasks.db")
