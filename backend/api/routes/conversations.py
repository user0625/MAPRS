from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from backend.api.ask_store import AskStore, MessageStatus
from backend.api.schemas import (
    AskAcceptedResponse,
    AskMessageCreate,
    AskMessageResponse,
    ConversationCreate,
    ConversationDetailResponse,
    ConversationListResponse,
    ConversationResponse,
    ConversationUpdate,
)
from backend.api.task_store import APITaskStatus
from backend.api import task_store as store_module
from backend.ask_paper import sections_from_state
from backend.core.config import get_settings

router = APIRouter(tags=["ask-paper"])


def stores():
    return store_module.task_store, AskStore(store_module.task_store)


def completed_task(task_id: str):
    tasks, _ = stores()
    task = tasks.get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found.")
    if task.status != APITaskStatus.COMPLETED:
        raise HTTPException(409, "Ask Paper requires a completed task.")
    return task


def conversation_or_404(conversation_id: str):
    _, ask = stores()
    conv = ask.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(404, "Conversation not found.")
    if not store_module.task_store.get_task(conv.task_id):
        raise HTTPException(404, "Conversation not found.")
    return conv


@router.post(
    "/api/tasks/{task_id}/conversations",
    response_model=ConversationResponse,
    status_code=201,
)
async def create_conversation(task_id: str, body: ConversationCreate) -> ConversationResponse:
    completed_task(task_id)
    _, ask = stores()
    return ConversationResponse.model_validate(
        ask.create_conversation(task_id, body.title, body.language),
        from_attributes=True,
    )


@router.get(
    "/api/tasks/{task_id}/conversations", response_model=ConversationListResponse
)
async def list_conversations(task_id: str) -> ConversationListResponse:
    completed_task(task_id)
    _, ask = stores()
    return ConversationListResponse(
        items=[
            ConversationResponse.model_validate(x, from_attributes=True)
            for x in ask.list_conversations(task_id)
        ]
    )


@router.get(
    "/api/conversations/{conversation_id}", response_model=ConversationDetailResponse
)
async def get_conversation(
    conversation_id: str,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    conv = conversation_or_404(conversation_id)
    _, ask = stores()
    messages, total = ask.messages(conv.id, limit, offset)
    return ConversationDetailResponse(
        **ConversationResponse.model_validate(conv, from_attributes=True).model_dump(),
        messages=[
            AskMessageResponse.model_validate(x, from_attributes=True) for x in messages
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.patch(
    "/api/conversations/{conversation_id}", response_model=ConversationResponse
)
async def update_conversation(
    conversation_id: str, body: ConversationUpdate
) -> ConversationResponse:
    conversation_or_404(conversation_id)
    _, ask = stores()
    row = ask.update_conversation_title(conversation_id, body.title)
    if not row:
        raise HTTPException(404, "Conversation not found.")
    return ConversationResponse.model_validate(row, from_attributes=True)


def validate_section(task, section: str | None):
    if not section:
        return
    if not task.state_json_path or not Path(task.state_json_path).is_file():
        raise HTTPException(409, "Paper state is unavailable.")
    state = json.loads(Path(task.state_json_path).read_text(encoding="utf-8"))
    if section not in sections_from_state(state):
        raise HTTPException(422, "Unknown paper section.")


@router.post(
    "/api/conversations/{conversation_id}/messages",
    response_model=AskAcceptedResponse,
    status_code=202,
)
async def create_message(conversation_id: str, body: AskMessageCreate):
    conv = conversation_or_404(conversation_id)
    task = completed_task(conv.task_id)
    validate_section(task, body.section)
    _, ask = stores()
    user, assistant = ask.create_exchange(
        conv.id, body.content.strip(), body.section, body.language
    )
    from backend.worker.tasks import enqueue_answer

    enqueue_answer(assistant.id)
    return AskAcceptedResponse(
        user_message_id=user.id if user else None,
        assistant_message_id=assistant.id,
        status=assistant.status,
    )


@router.post(
    "/api/conversations/{conversation_id}/messages/{message_id}/cancel",
    response_model=AskMessageResponse,
)
async def cancel_message(conversation_id: str, message_id: str):
    conversation_or_404(conversation_id)
    _, ask = stores()
    row = ask.get_message(message_id)
    if not row or row.conversation_id != conversation_id:
        raise HTTPException(404, "Message not found.")
    if row.status != MessageStatus.GENERATING:
        raise HTTPException(409, "Only a generating answer can be canceled.")
    return AskMessageResponse.model_validate(
        ask.request_cancel(message_id), from_attributes=True
    )


@router.post(
    "/api/conversations/{conversation_id}/messages/{message_id}/retry",
    response_model=AskAcceptedResponse,
    status_code=202,
)
async def retry_message(conversation_id: str, message_id: str):
    conversation_or_404(conversation_id)
    _, ask = stores()
    source = ask.get_message(message_id)
    if (
        not source
        or source.conversation_id != conversation_id
        or source.role != "assistant"
    ):
        raise HTTPException(404, "Assistant message not found.")
    if source.status not in (MessageStatus.FAILED, MessageStatus.CANCELED):
        raise HTTPException(409, "Only failed or canceled answers can be retried.")
    _, assistant = ask.create_exchange(
        conversation_id, "", source.section, source.language, retry_of=source.id
    )
    from backend.worker.tasks import enqueue_answer

    enqueue_answer(assistant.id)
    return AskAcceptedResponse(
        user_message_id=None, assistant_message_id=assistant.id, status=assistant.status
    )


@router.get("/api/conversations/{conversation_id}/messages/{message_id}/events")
async def message_events(
    conversation_id: str,
    message_id: str,
    after: int = Query(0, ge=0),
    last: str | None = Header(None, alias="Last-Event-ID"),
):
    conversation_or_404(conversation_id)
    _, ask = stores()
    message = ask.get_message(message_id)
    if (
        not message
        or message.conversation_id != conversation_id
        or message.role != "assistant"
    ):
        raise HTTPException(404, "Message not found.")
    try:
        cursor = max(after, int(last or 0))
    except ValueError:
        cursor = after

    async def stream():
        nonlocal cursor
        while True:
            for event in ask.events(message_id, cursor):
                cursor = event.sequence
                yield f"id: {cursor}\nevent: {event.event_type}\ndata: {json.dumps(event.data, ensure_ascii=False)}\n\n"
                if event.event_type in {"completed", "failed", "canceled"}:
                    return
            current = ask.get_message(message_id)
            if current and current.status != MessageStatus.GENERATING:
                return
            yield f"event: heartbeat\ndata: {json.dumps({'after': cursor})}\n\n"
            await asyncio.sleep(get_settings().sse_heartbeat_seconds)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
