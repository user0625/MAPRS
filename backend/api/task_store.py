from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

from sqlalchemy import JSON, Column, UniqueConstraint, inspect, text, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Session, SQLModel, create_engine, func, select


def utcnow() -> datetime:
    return datetime.utcnow()


JSON_TYPE = JSON().with_variant(JSONB, "postgresql")


class APITaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    INTERRUPTED = "interrupted"


TERMINAL_STATUSES = (
    APITaskStatus.COMPLETED,
    APITaskStatus.FAILED,
    APITaskStatus.CANCELED,
)


class APITaskRecord(SQLModel, table=True):
    __tablename__ = "api_tasks"

    task_id: str = Field(primary_key=True)
    status: APITaskStatus = Field(default=APITaskStatus.PENDING, index=True)
    message: str = "Task created."
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    last_progress_at: datetime | None = None
    worker_heartbeat_at: datetime | None = None
    input_pdf_path: str | None = None
    report_path: str | None = None
    state_json_path: str | None = None
    paper_title: str | None = None
    paper_id: str | None = None
    error_message: str | None = None
    file_sha256: str | None = Field(default=None, index=True)
    dedup_key: str | None = Field(default=None, index=True)
    retry_of: str | None = None
    rerun_of: str | None = None
    celery_task_id: str | None = None
    attempt_count: int = 0
    progress: int = 0
    current_step: str | None = None
    last_checkpoint_step: str | None = None
    last_event_id: int = 0
    cancel_requested_at: datetime | None = None
    cleaned_at: datetime | None = None
    deleted_at: datetime | None = Field(default=None, index=True)
    delete_reason: str | None = None
    task_metadata: dict[str, Any] = Field(
        default_factory=dict,
        serialization_alias="metadata",
        sa_column=Column("metadata", JSON_TYPE),
    )


class TaskCheckpoint(SQLModel, table=True):
    __tablename__ = "task_checkpoints"
    __table_args__ = (UniqueConstraint("task_id", "step", "schema_version"),)

    id: int | None = Field(default=None, primary_key=True)
    task_id: str = Field(index=True)
    step: str = Field(index=True)
    schema_version: int = 1
    state: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON_TYPE))
    created_at: datetime = Field(default_factory=utcnow)


class TaskEvent(SQLModel, table=True):
    __tablename__ = "task_events"
    __table_args__ = (UniqueConstraint("task_id", "sequence"),)

    id: int | None = Field(default=None, primary_key=True)
    task_id: str = Field(index=True)
    sequence: int = Field(index=True)
    event_type: str = Field(index=True)
    status: str | None = None
    step: str | None = None
    message: str | None = None
    event_metadata: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column("metadata", JSON_TYPE)
    )
    created_at: datetime = Field(default_factory=utcnow, index=True)


class AuditEvent(SQLModel, table=True):
    __tablename__ = "audit_events"
    id: int | None = Field(default=None, primary_key=True)
    task_id: str = Field(index=True)
    action: str = Field(index=True)
    actor: str = "system"
    audit_metadata: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column("metadata", JSON_TYPE)
    )
    created_at: datetime = Field(default_factory=utcnow, index=True)


class DatabaseTaskStore:
    """Persistent task/checkpoint/event store supporting SQLite and PostgreSQL."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        if database_url.startswith("sqlite:///"):
            database_path = database_url.removeprefix("sqlite:///")
            if database_path != ":memory:":
                Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            database_url,
            pool_pre_ping=True,
            connect_args={"check_same_thread": False}
            if database_url.startswith("sqlite")
            else {},
        )

    def create_tables(self) -> None:
        SQLModel.metadata.create_all(self.engine)
        if self.database_url.startswith("sqlite"):
            self._migrate_columns()

    def _migrate_columns(self) -> None:
        if not inspect(self.engine).has_table("api_tasks"):
            return
        columns = {
            item["name"] for item in inspect(self.engine).get_columns("api_tasks")
        }
        definitions = {
            "file_sha256": "VARCHAR",
            "dedup_key": "VARCHAR",
            "retry_of": "VARCHAR",
            "rerun_of": "VARCHAR",
            "celery_task_id": "VARCHAR",
            "attempt_count": "INTEGER DEFAULT 0",
            "progress": "INTEGER DEFAULT 0",
            "current_step": "VARCHAR",
            "last_checkpoint_step": "VARCHAR",
            "last_event_id": "INTEGER DEFAULT 0",
            "cancel_requested_at": "DATETIME",
            "cleaned_at": "DATETIME",
            "deleted_at": "DATETIME",
            "delete_reason": "VARCHAR",
            "started_at": "DATETIME",
            "last_progress_at": "DATETIME",
            "worker_heartbeat_at": "DATETIME",
        }
        with self.engine.begin() as connection:
            for name, definition in definitions.items():
                if name not in columns:
                    connection.execute(
                        text(f"ALTER TABLE api_tasks ADD COLUMN {name} {definition}")
                    )

    def create_task(
        self,
        task_id: str,
        input_pdf_path: str,
        metadata: dict[str, Any] | None = None,
        file_sha256: str | None = None,
        dedup_key: str | None = None,
        retry_of: str | None = None,
        rerun_of: str | None = None,
    ) -> APITaskRecord:
        existing = self.get_task(task_id, include_deleted=True)
        if existing:
            return existing
        record = APITaskRecord(
            task_id=task_id,
            input_pdf_path=input_pdf_path,
            task_metadata=metadata or {},
            file_sha256=file_sha256,
            dedup_key=dedup_key,
            retry_of=retry_of,
            rerun_of=rerun_of,
        )
        with Session(self.engine) as session:
            session.add(record)
            session.commit()
            session.refresh(record)
        self.add_event(task_id, "queued", APITaskStatus.PENDING, message="Task queued.")
        self.audit(task_id, "created", "anonymous")
        return self.get_task(task_id, include_deleted=True) or record

    def get_task(
        self, task_id: str, include_deleted: bool = False
    ) -> APITaskRecord | None:
        with Session(self.engine) as session:
            record = session.get(APITaskRecord, task_id)
            return (
                None if record and record.deleted_at and not include_deleted else record
            )

    def list_tasks(
        self,
        limit: int = 20,
        offset: int = 0,
        status: str | None = None,
        search: str | None = None,
    ) -> tuple[list[APITaskRecord], int]:
        with Session(self.engine) as session:
            filters = [APITaskRecord.deleted_at.is_(None)]
            if status:
                filters.append(APITaskRecord.status == status)
            if search:
                pattern = f"%{search}%"
                filters.append(
                    (APITaskRecord.paper_title.ilike(pattern))
                    | (APITaskRecord.task_id.ilike(pattern))
                )
            total = session.exec(
                select(func.count()).select_from(APITaskRecord).where(*filters)
            ).one()
            statement = (
                select(APITaskRecord)
                .where(*filters)
                .order_by(APITaskRecord.created_at.desc(), APITaskRecord.task_id.desc())
                .offset(offset)
                .limit(limit)
            )
            return list(session.exec(statement).all()), total

    def claim_task(self, task_id: str) -> bool:
        with Session(self.engine) as session:
            now = utcnow()
            result = session.exec(
                update(APITaskRecord)
                .where(
                    APITaskRecord.task_id == task_id,
                    APITaskRecord.deleted_at.is_(None),
                    APITaskRecord.status == APITaskStatus.PENDING,
                )
                .values(
                    status=APITaskStatus.RUNNING,
                    attempt_count=APITaskRecord.attempt_count + 1,
                    started_at=func.coalesce(APITaskRecord.started_at, now),
                    worker_heartbeat_at=now,
                    updated_at=now,
                    message="Paper analysis is running.",
                )
            )
            session.commit()
            if result.rowcount != 1:
                return False
            record = session.get(APITaskRecord, task_id)
            attempt = record.attempt_count if record else 1
        self.add_event(
            task_id,
            "started",
            APITaskStatus.RUNNING,
            metadata={"attempt": attempt},
        )
        self.audit(task_id, "started")
        return True

    def mark_running(self, task_id: str, message: str = "Task is running.") -> None:
        self._update(
            task_id,
            status=APITaskStatus.RUNNING,
            message=message,
            started_at=utcnow(),
            worker_heartbeat_at=utcnow(),
        )

    def set_celery_task_id(self, task_id: str, celery_task_id: str) -> None:
        self._update(task_id, celery_task_id=celery_task_id)
        self.audit(task_id, "enqueued", metadata={"celery_task_id": celery_task_id})

    def heartbeat(self, task_id: str) -> None:
        self._update(task_id, worker_heartbeat_at=utcnow())

    def save_checkpoint(
        self,
        task_id: str,
        step: str,
        state: dict[str, Any],
        schema_version: int,
        progress: int,
    ) -> None:
        with Session(self.engine) as session:
            checkpoint = session.exec(
                select(TaskCheckpoint).where(
                    TaskCheckpoint.task_id == task_id,
                    TaskCheckpoint.step == step,
                    TaskCheckpoint.schema_version == schema_version,
                )
            ).first()
            if checkpoint:
                checkpoint.state = state
                checkpoint.created_at = utcnow()
            else:
                checkpoint = TaskCheckpoint(
                    task_id=task_id,
                    step=step,
                    state=state,
                    schema_version=schema_version,
                )
            session.add(checkpoint)
            session.commit()
        self._update(
            task_id,
            last_checkpoint_step=step,
            current_step=step,
            progress=progress,
            last_progress_at=utcnow(),
            worker_heartbeat_at=utcnow(),
        )
        self.add_event(
            task_id,
            "checkpointed",
            APITaskStatus.RUNNING,
            step,
            "Stage checkpoint saved.",
            {"progress": progress, "schema_version": schema_version},
        )
        self.add_event(
            task_id,
            "progress",
            APITaskStatus.RUNNING,
            step,
            metadata={"progress": progress},
        )
        self.audit(
            task_id, "stage_completed", metadata={"step": step, "progress": progress}
        )

    def latest_checkpoint(self, task_id: str) -> TaskCheckpoint | None:
        with Session(self.engine) as session:
            return session.exec(
                select(TaskCheckpoint)
                .where(TaskCheckpoint.task_id == task_id)
                .order_by(TaskCheckpoint.created_at.desc(), TaskCheckpoint.id.desc())
            ).first()

    def add_event(
        self,
        task_id: str,
        event_type: str,
        status: APITaskStatus | str | None = None,
        step: str | None = None,
        message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TaskEvent:
        with Session(self.engine) as session:
            record = session.get(APITaskRecord, task_id)
            sequence = (record.last_event_id if record else 0) + 1
            event = TaskEvent(
                task_id=task_id,
                sequence=sequence,
                event_type=event_type,
                status=status.value if isinstance(status, APITaskStatus) else status,
                step=step,
                message=message,
                event_metadata=metadata or {},
            )
            session.add(event)
            if record:
                record.last_event_id = sequence
                record.updated_at = utcnow()
                session.add(record)
            session.commit()
            session.refresh(event)
            return event

    def list_events(
        self, task_id: str, after: int = 0, limit: int = 1000
    ) -> list[TaskEvent]:
        with Session(self.engine) as session:
            return list(
                session.exec(
                    select(TaskEvent)
                    .where(TaskEvent.task_id == task_id, TaskEvent.sequence > after)
                    .order_by(TaskEvent.sequence)
                    .limit(limit)
                ).all()
            )

    def audit(
        self,
        task_id: str,
        action: str,
        actor: str = "system",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with Session(self.engine) as session:
            session.add(
                AuditEvent(
                    task_id=task_id,
                    action=action,
                    actor=actor,
                    audit_metadata=metadata or {},
                )
            )
            session.commit()

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
            completed_at=utcnow(),
            progress=100,
            current_step="export",
            metadata_update=metadata,
        )
        self.add_event(
            task_id,
            "completed",
            APITaskStatus.COMPLETED,
            "export",
            message,
            {"progress": 100},
        )
        self.audit(task_id, "completed")

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
            completed_at=utcnow(),
        )
        self.add_event(task_id, "failed", APITaskStatus.FAILED, message=message)
        self.audit(task_id, "failed")

    def find_active_by_dedup_key(self, dedup_key: str) -> APITaskRecord | None:
        with Session(self.engine) as session:
            record = session.exec(
                select(APITaskRecord)
                .where(
                    APITaskRecord.dedup_key == dedup_key,
                    APITaskRecord.deleted_at.is_(None),
                    APITaskRecord.status.in_(
                        [APITaskStatus.PENDING, APITaskStatus.RUNNING]
                    ),
                )
                .order_by(APITaskRecord.created_at.desc())
            ).first()
            return (
                record
                if record
                and record.input_pdf_path
                and Path(record.input_pdf_path).is_file()
                else None
            )

    def request_cancel(self, task_id: str) -> APITaskRecord | None:
        with Session(self.engine) as session:
            record = session.get(APITaskRecord, task_id)
            if record is None or record.deleted_at:
                return None
            if record.status in (APITaskStatus.PENDING, APITaskStatus.RUNNING):
                record.cancel_requested_at = utcnow()
                record.message = "Cancellation requested."
                if record.status == APITaskStatus.PENDING:
                    record.status = APITaskStatus.CANCELED
                    record.completed_at = utcnow()
                record.updated_at = utcnow()
                session.add(record)
                session.commit()
                session.refresh(record)
        if record.status == APITaskStatus.CANCELED:
            self.add_event(
                task_id, "canceled", APITaskStatus.CANCELED, message="Task canceled."
            )
        self.audit(task_id, "cancel_requested", "anonymous")
        return record

    def is_cancel_requested(self, task_id: str) -> bool:
        record = self.get_task(task_id)
        return bool(
            record
            and (record.cancel_requested_at or record.status == APITaskStatus.CANCELED)
        )

    def mark_canceled(self, task_id: str, state_json_path: str | None = None) -> None:
        self._update(
            task_id,
            status=APITaskStatus.CANCELED,
            message="Task canceled.",
            error_message=None,
            state_json_path=state_json_path,
            completed_at=utcnow(),
        )
        self.add_event(
            task_id, "canceled", APITaskStatus.CANCELED, message="Task canceled."
        )
        self.audit(task_id, "canceled")

    def prepare_resume(self, task_id: str) -> APITaskRecord | None:
        with Session(self.engine) as session:
            record = session.get(APITaskRecord, task_id)
            if not record or record.deleted_at:
                return None
            record.status = APITaskStatus.PENDING
            record.completed_at = None
            record.error_message = None
            record.cancel_requested_at = None
            record.message = "Task queued for checkpoint resume."
            record.updated_at = utcnow()
            session.add(record)
            session.commit()
            session.refresh(record)
        self.add_event(
            task_id,
            "queued",
            APITaskStatus.PENDING,
            message=record.message,
            metadata={"resume": True, "next_attempt": record.attempt_count + 1},
        )
        self.audit(task_id, "resumed", "anonymous")
        return record

    def soft_delete(self, task_id: str, reason: str = "Deleted by user") -> bool:
        record = self.get_task(task_id)
        if not record or record.status not in TERMINAL_STATUSES:
            return False
        for raw in (record.input_pdf_path, record.report_path, record.state_json_path):
            if raw:
                path = Path(raw)
                path.unlink(missing_ok=True)
                if raw == record.report_path:
                    for suffix in (".json", ".html", ".pdf", ".docx"):
                        path.with_suffix(suffix).unlink(missing_ok=True)
        self.add_event(task_id, "deleted", record.status, message=reason)
        self.audit(task_id, "deleted", "anonymous", {"reason": reason})
        self._update(
            task_id,
            deleted_at=utcnow(),
            delete_reason=reason,
            input_pdf_path=None,
            report_path=None,
            state_json_path=None,
            task_metadata={},
        )
        return True

    def cleanup_expired_files(self, retention_days: int) -> int:
        cutoff = utcnow() - timedelta(days=retention_days)
        count = 0
        with Session(self.engine) as session:
            records = session.exec(
                select(APITaskRecord).where(
                    APITaskRecord.status.in_(TERMINAL_STATUSES),
                    APITaskRecord.completed_at <= cutoff,
                    APITaskRecord.cleaned_at.is_(None),
                )
            ).all()
            for record in records:
                for raw in (
                    record.input_pdf_path,
                    record.report_path,
                    record.state_json_path,
                ):
                    if raw and Path(raw).name.startswith(("task_", "api_")):
                        Path(raw).unlink(missing_ok=True)
                record.cleaned_at = utcnow()
                record.updated_at = utcnow()
                record.task_metadata = {**record.task_metadata, "files_cleaned": True}
                session.add(record)
                count += 1
            session.commit()
        return count

    def recover_interrupted_tasks(self, stale_after_seconds: int = 0) -> int:
        cutoff = utcnow() - timedelta(seconds=stale_after_seconds)
        now = utcnow()
        with Session(self.engine) as session:
            query = select(APITaskRecord).where(
                APITaskRecord.status.in_([APITaskStatus.PENDING, APITaskStatus.RUNNING])
            )
            tasks = session.exec(query).all()
            stale = [
                t
                for t in tasks
                if stale_after_seconds == 0
                or (t.worker_heartbeat_at or t.updated_at) <= cutoff
            ]
            for task in stale:
                task.status = APITaskStatus.FAILED
                task.message = "Task interrupted by worker loss."
                task.error_message = (
                    "The worker or service restarted before the task completed."
                )
                task.updated_at = now
                task.completed_at = now
                session.add(task)
            event_data = [(task.task_id, task.message) for task in stale]
            session.commit()
        for task_id, message in event_data:
            self.add_event(task_id, "failed", APITaskStatus.FAILED, message=message)
        return len(event_data)

    def _update(
        self,
        task_id: str,
        metadata_update: dict[str, Any] | None = None,
        **updates: Any,
    ) -> None:
        with Session(self.engine) as session:
            record = session.get(APITaskRecord, task_id)
            if record is None:
                return
            for key, value in updates.items():
                setattr(record, key, value)
            if metadata_update:
                record.task_metadata = {**record.task_metadata, **metadata_update}
            record.updated_at = utcnow()
            session.add(record)
            session.commit()


task_store = DatabaseTaskStore("sqlite:///backend/data/tasks.db")
