from __future__ import annotations

import json
from typing import Any

from backend.api.ask_store import MessageEvidence, PaperConversation, PaperMessage


SCHEMA_VERSION = "ask-paper-conversation-v1"


def _iso(value) -> str:
    return value.isoformat()


def _message_json(message: PaperMessage) -> dict[str, Any]:
    return {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "role": message.role,
        "content": message.content,
        "status": message.status.value
        if hasattr(message.status, "value")
        else str(message.status),
        "language": message.language,
        "section": message.section,
        "citation_ids": list(message.citation_ids),
        "error": message.error,
        "retry_of": message.retry_of,
        "created_at": _iso(message.created_at),
        "updated_at": _iso(message.updated_at),
    }


def _evidence_json(item: MessageEvidence) -> dict[str, Any]:
    return {
        "evidence_id": item.evidence_id,
        "message_id": item.message_id,
        "chunk_id": item.chunk_id,
        "page_start": item.page_start,
        "page_end": item.page_end,
        "section": item.section,
        "text": item.text,
        "score": item.score,
        "created_at": _iso(item.created_at),
    }


def export_json(
    conversation: PaperConversation,
    messages: list[PaperMessage],
    evidence: list[MessageEvidence],
) -> str:
    archive = {
        "schema_version": SCHEMA_VERSION,
        "conversation": {
            "id": conversation.id,
            "task_id": conversation.task_id,
            "title": conversation.title,
            "language": conversation.language,
            "created_at": _iso(conversation.created_at),
            "updated_at": _iso(conversation.updated_at),
        },
        "messages": [_message_json(message) for message in messages],
        "evidence": [_evidence_json(item) for item in evidence],
    }
    return json.dumps(archive, ensure_ascii=False, indent=2) + "\n"


def _metadata(label: str, value: object | None) -> str:
    shown = "—" if value is None or value == "" else str(value)
    return f"- **{label}:** `{shown}`"


def _pages(item: MessageEvidence) -> str:
    if item.page_start is None and item.page_end is None:
        return "—"
    if item.page_end is None or item.page_end == item.page_start:
        return str(item.page_start)
    if item.page_start is None:
        return str(item.page_end)
    return f"{item.page_start}–{item.page_end}"


def export_markdown(
    conversation: PaperConversation,
    messages: list[PaperMessage],
    evidence: list[MessageEvidence],
) -> str:
    lines = [
        f"# {conversation.title}",
        "",
        _metadata("Task ID", conversation.task_id),
        _metadata("Conversation ID", conversation.id),
        _metadata("Language", conversation.language),
        _metadata("Created", _iso(conversation.created_at)),
        _metadata("Updated", _iso(conversation.updated_at)),
        "",
        "## Conversation",
        "",
    ]
    if not messages:
        lines.extend(["_No messages._", ""])
    for index, message in enumerate(messages, start=1):
        role = "User" if message.role == "user" else "Assistant"
        citation_ids = ", ".join(message.citation_ids) or "—"
        status = (
            message.status.value
            if hasattr(message.status, "value")
            else str(message.status)
        )
        lines.extend(
            [
                f"### {index}. {role}",
                "",
                _metadata("Message ID", message.id),
                _metadata("Status", status),
                _metadata("Language", message.language),
                _metadata("Section", message.section),
                _metadata("Citation IDs", citation_ids),
                _metadata("Created", _iso(message.created_at)),
                "",
                message.content,
                "",
            ]
        )
        if message.error:
            lines.extend([f"> Error: {message.error}", ""])

    lines.extend(["## Evidence", ""])
    if not evidence:
        lines.extend(["_No cited evidence._", ""])
    for item in evidence:
        lines.extend(
            [
                f"### {item.evidence_id}",
                "",
                _metadata("Message ID", item.message_id),
                _metadata("Chunk ID", item.chunk_id),
                _metadata("Pages", _pages(item)),
                _metadata("Section", item.section),
                _metadata("Retrieval score", item.score),
                "",
                item.text,
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"
