from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

from sqlalchemy import JSON, Column, inspect, text
from sqlmodel import Field, Session, SQLModel, create_engine, func, select


class APITaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


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
    file_sha256: str | None = Field(default=None, index=True)
    dedup_key: str | None = Field(default=None, index=True)
    retry_of: str | None = None
    cancel_requested_at: datetime | None = None
    cleaned_at: datetime | None = None
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
        self._migrate_columns()

    def _migrate_columns(self) -> None:
        columns = {item["name"] for item in inspect(self.engine).get_columns("api_tasks")}
        definitions = {"file_sha256": "VARCHAR", "dedup_key": "VARCHAR", "retry_of": "VARCHAR",
                       "cancel_requested_at": "DATETIME", "cleaned_at": "DATETIME"}
        with self.engine.begin() as connection:
            for name, definition in definitions.items():
                if name not in columns:
                    connection.execute(text(f"ALTER TABLE api_tasks ADD COLUMN {name} {definition}"))

    def create_task(
        self, task_id: str, input_pdf_path: str, metadata: dict[str, Any] | None = None,
        file_sha256: str | None = None, dedup_key: str | None = None,
        retry_of: str | None = None,
    ) -> APITaskRecord:
        record = APITaskRecord(
            task_id=task_id, input_pdf_path=input_pdf_path, task_metadata=metadata or {},
            file_sha256=file_sha256, dedup_key=dedup_key, retry_of=retry_of,
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

    def find_active_by_dedup_key(self, dedup_key: str) -> APITaskRecord | None:
        with Session(self.engine) as session:
            return session.exec(select(APITaskRecord).where(
                APITaskRecord.dedup_key == dedup_key,
                APITaskRecord.status.in_([APITaskStatus.PENDING, APITaskStatus.RUNNING]),
            ).order_by(APITaskRecord.created_at.desc())).first()

    def request_cancel(self, task_id: str) -> APITaskRecord | None:
        with Session(self.engine) as session:
            record = session.get(APITaskRecord, task_id)
            if record is None:
                return None
            if record.status in (APITaskStatus.PENDING, APITaskStatus.RUNNING):
                record.cancel_requested_at = datetime.utcnow()
                record.message = "Cancellation requested."
                if record.status == APITaskStatus.PENDING:
                    record.status = APITaskStatus.CANCELED
                    record.completed_at = datetime.utcnow()
                record.updated_at = datetime.utcnow()
                session.add(record)
                session.commit()
                session.refresh(record)
            return record

    def is_cancel_requested(self, task_id: str) -> bool:
        record = self.get_task(task_id)
        return bool(record and (record.cancel_requested_at or record.status == APITaskStatus.CANCELED))

    def mark_canceled(self, task_id: str, state_json_path: str | None = None) -> None:
        self._update(task_id, status=APITaskStatus.CANCELED, message="Task canceled.",
                     error_message=None, state_json_path=state_json_path,
                     completed_at=datetime.utcnow())

    def cleanup_expired_files(self, retention_days: int) -> int:
        cutoff = datetime.utcnow() - timedelta(days=retention_days)
        count = 0
        with Session(self.engine) as session:
            records = session.exec(select(APITaskRecord).where(
                APITaskRecord.status.in_([APITaskStatus.COMPLETED, APITaskStatus.FAILED, APITaskStatus.CANCELED]),
                APITaskRecord.completed_at <= cutoff, APITaskRecord.cleaned_at.is_(None))).all()
            for record in records:
                for raw in (record.input_pdf_path, record.report_path, record.state_json_path):
                    if not raw:
                        continue
                    path = Path(raw)
                    if path.name.startswith(("task_", "api_")):
                        path.unlink(missing_ok=True)
                        if raw == record.report_path:
                            for suffix in (".json", ".html", ".pdf", ".docx"):
                                path.with_suffix(suffix).unlink(missing_ok=True)
                record.cleaned_at = datetime.utcnow()
                record.updated_at = datetime.utcnow()
                record.task_metadata = {**record.task_metadata, "files_cleaned": True}
                session.add(record)
                count += 1
            session.commit()
        return count

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
