from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import Column, UniqueConstraint
from sqlmodel import Field, Session, SQLModel, func, select

from backend.api.task_store import JSON_TYPE, DatabaseTaskStore, utcnow


class MessageStatus(str, Enum):
    COMPLETED = "completed"
    GENERATING = "generating"
    FAILED = "failed"
    CANCELED = "canceled"


class PaperConversation(SQLModel, table=True):
    __tablename__ = "paper_conversations"
    id: str = Field(primary_key=True)
    task_id: str = Field(index=True)
    title: str = "New conversation"
    language: str = "auto"
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow, index=True)


class PaperMessage(SQLModel, table=True):
    __tablename__ = "paper_messages"
    id: str = Field(primary_key=True)
    conversation_id: str = Field(index=True)
    role: str = Field(index=True)
    content: str = ""
    status: MessageStatus = Field(default=MessageStatus.COMPLETED, index=True)
    language: str = "auto"
    section: str | None = None
    citation_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON_TYPE))
    error: str | None = None
    retry_of: str | None = None
    cancel_requested_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow)


class MessageEvidence(SQLModel, table=True):
    __tablename__ = "message_evidence"
    evidence_id: str = Field(primary_key=True)
    message_id: str = Field(index=True)
    task_id: str = Field(index=True)
    chunk_id: str | None = None
    text: str
    page_start: int | None = None
    page_end: int | None = None
    section: str | None = None
    score: float | None = None
    created_at: datetime = Field(default_factory=utcnow)


class MessageStreamEvent(SQLModel, table=True):
    __tablename__ = "message_stream_events"
    __table_args__ = (UniqueConstraint("message_id", "sequence"),)
    id: int | None = Field(default=None, primary_key=True)
    message_id: str = Field(index=True)
    sequence: int = Field(index=True)
    event_type: str = Field(index=True)
    data: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON_TYPE))
    created_at: datetime = Field(default_factory=utcnow)


class AskStore:
    def __init__(self, tasks: DatabaseTaskStore):
        self.tasks = tasks

    @property
    def engine(self):
        return self.tasks.engine

    def create_conversation(
        self, task_id: str, title: str | None = None, language: str = "auto"
    ) -> PaperConversation:
        row = PaperConversation(
            id=f"conv_{uuid.uuid4().hex[:16]}",
            task_id=task_id,
            title=(title or "New conversation").strip()[:200],
            language=language,
        )
        with Session(self.engine) as session:
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def list_conversations(self, task_id: str) -> list[PaperConversation]:
        with Session(self.engine) as session:
            return list(
                session.exec(
                    select(PaperConversation)
                    .where(PaperConversation.task_id == task_id)
                    .order_by(PaperConversation.updated_at.desc())
                ).all()
            )

    def get_conversation(self, conversation_id: str) -> PaperConversation | None:
        with Session(self.engine) as session:
            return session.get(PaperConversation, conversation_id)

    def update_conversation_title(
        self, conversation_id: str, title: str
    ) -> PaperConversation | None:
        with Session(self.engine) as session:
            row = session.get(PaperConversation, conversation_id)
            if not row:
                return None
            row.title = title.strip()[:200]
            row.updated_at = utcnow()
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def messages(
        self, conversation_id: str, limit: int = 50, offset: int = 0
    ) -> tuple[list[PaperMessage], int]:
        with Session(self.engine) as session:
            total = session.exec(
                select(func.count())
                .select_from(PaperMessage)
                .where(PaperMessage.conversation_id == conversation_id)
            ).one()
            rows = session.exec(
                select(PaperMessage)
                .where(PaperMessage.conversation_id == conversation_id)
                .order_by(PaperMessage.created_at, PaperMessage.id)
                .offset(offset)
                .limit(limit)
            ).all()
            return list(rows), total

    def create_exchange(
        self,
        conversation_id: str,
        question: str,
        section: str | None,
        language: str,
        retry_of: str | None = None,
    ) -> tuple[PaperMessage | None, PaperMessage]:
        now = utcnow()
        user = (
            None
            if retry_of
            else PaperMessage(
                id=f"msg_{uuid.uuid4().hex[:16]}",
                conversation_id=conversation_id,
                role="user",
                content=question,
                language=language,
                section=section,
                created_at=now,
                updated_at=now,
            )
        )
        assistant = PaperMessage(
            id=f"msg_{uuid.uuid4().hex[:16]}",
            conversation_id=conversation_id,
            role="assistant",
            status=MessageStatus.GENERATING,
            language=language,
            section=section,
            retry_of=retry_of,
            created_at=now,
            updated_at=now,
        )
        with Session(self.engine) as session:
            if user:
                session.add(user)
            session.add(assistant)
            conv = session.get(PaperConversation, conversation_id)
            if conv:
                conv.updated_at = now
                if user and conv.title == "New conversation":
                    conv.title = question[:80]
                session.add(conv)
            session.commit()
            if user:
                session.refresh(user)
            session.refresh(assistant)
        return user, assistant

    def get_message(self, message_id: str) -> PaperMessage | None:
        with Session(self.engine) as session:
            return session.get(PaperMessage, message_id)

    def append_event(
        self, message_id: str, event_type: str, data: dict[str, Any]
    ) -> MessageStreamEvent:
        with Session(self.engine) as session:
            last = (
                session.exec(
                    select(func.max(MessageStreamEvent.sequence)).where(
                        MessageStreamEvent.message_id == message_id
                    )
                ).one()
                or 0
            )
            row = MessageStreamEvent(
                message_id=message_id,
                sequence=last + 1,
                event_type=event_type,
                data=data,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def events(self, message_id: str, after: int = 0) -> list[MessageStreamEvent]:
        with Session(self.engine) as session:
            return list(
                session.exec(
                    select(MessageStreamEvent)
                    .where(
                        MessageStreamEvent.message_id == message_id,
                        MessageStreamEvent.sequence > after,
                    )
                    .order_by(MessageStreamEvent.sequence)
                ).all()
            )

    def finish(
        self, message_id: str, content: str, evidence: list[dict[str, Any]]
    ) -> None:
        citations = [item["evidence_id"] for item in evidence]
        with Session(self.engine) as session:
            row = session.get(PaperMessage, message_id)
            if not row:
                return
            row.content, row.citation_ids, row.status, row.updated_at = (
                content,
                citations,
                MessageStatus.COMPLETED,
                utcnow(),
            )
            session.add(row)
            for item in evidence:
                session.add(MessageEvidence(message_id=message_id, **item))
            session.commit()
        self.append_event(
            message_id, "completed", {"content": content, "citation_ids": citations}
        )

    def fail(self, message_id: str, error: str) -> None:
        with Session(self.engine) as session:
            row = session.get(PaperMessage, message_id)
            if not row:
                return
            row.status, row.error, row.updated_at = (
                MessageStatus.FAILED,
                error[:1000],
                utcnow(),
            )
            session.add(row)
            session.commit()
        self.append_event(message_id, "failed", {"error": error[:1000]})

    def request_cancel(self, message_id: str) -> PaperMessage | None:
        with Session(self.engine) as session:
            row = session.get(PaperMessage, message_id)
            if row and row.status == MessageStatus.GENERATING:
                row.cancel_requested_at = utcnow()
                session.add(row)
                session.commit()
                session.refresh(row)
            return row

    def is_canceled(self, message_id: str) -> bool:
        row = self.get_message(message_id)
        return bool(row and row.cancel_requested_at)

    def mark_canceled(self, message_id: str) -> None:
        with Session(self.engine) as session:
            row = session.get(PaperMessage, message_id)
            if not row:
                return
            row.status, row.updated_at = MessageStatus.CANCELED, utcnow()
            session.add(row)
            session.commit()
        self.append_event(message_id, "canceled", {})

    def evidence(self, task_id: str, evidence_id: str) -> MessageEvidence | None:
        with Session(self.engine) as session:
            return session.exec(
                select(MessageEvidence).where(
                    MessageEvidence.task_id == task_id,
                    MessageEvidence.evidence_id == evidence_id,
                )
            ).first()

    def delete_task_data(self, task_id: str) -> None:
        with Session(self.engine) as session:
            convs = session.exec(
                select(PaperConversation).where(PaperConversation.task_id == task_id)
            ).all()
            for conv in convs:
                messages = session.exec(
                    select(PaperMessage).where(PaperMessage.conversation_id == conv.id)
                ).all()
                for message in messages:
                    for event in session.exec(
                        select(MessageStreamEvent).where(
                            MessageStreamEvent.message_id == message.id
                        )
                    ).all():
                        session.delete(event)
                    for ev in session.exec(
                        select(MessageEvidence).where(
                            MessageEvidence.message_id == message.id
                        )
                    ).all():
                        session.delete(ev)
                    session.delete(message)
                session.delete(conv)
            session.commit()
