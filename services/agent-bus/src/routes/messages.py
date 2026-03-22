"""Legacy message routes (pre-turns API)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.auth import require_token
from src.db import get_messages, insert_message, mark_read, normalize_thread_id
from src.models import AgentName, Message, MessageCreate, MessageCreated, MessageList

router = APIRouter(dependencies=[Depends(require_token)])


@router.post(
    "/messages",
    status_code=status.HTTP_201_CREATED,
    response_model=MessageCreated,
)
async def send_message(msg: MessageCreate) -> MessageCreated:
    """Persist one legacy message and return its new identifier and timestamp."""
    msg.thread = normalize_thread_id(msg.thread)
    msg_id, ts = insert_message(
        from_agent=msg.from_agent,
        to_agent=msg.to,
        thread=msg.thread,
        body=msg.body,
    )
    return MessageCreated(id=msg_id, timestamp=datetime.fromisoformat(ts))


@router.get(
    "/messages",
    response_model=MessageList,
)
async def get_messages_route(
    to: AgentName = Query(...),
    thread: str | None = Query(None),
    since: int | None = Query(None),
    unread: bool = Query(False),
) -> MessageList:
    """List legacy messages filtered by recipient, thread, offset, and unread state."""
    if thread is not None:
        thread = normalize_thread_id(thread)
    rows = get_messages(to=to, thread=thread, since=since, unread=unread)
    messages = [
        Message.model_validate(
            {
                "id": r["id"],
                "from": r["from_agent"],
                "to": r["to_agent"],
                "thread": r["thread"],
                "body": r["body"],
                "timestamp": r["timestamp"],
                "read": bool(r["read"]),
            }
        )
        for r in rows
    ]
    return MessageList(messages=messages)


@router.post("/messages/{message_id}/read")
async def mark_message_read(message_id: int) -> dict[str, str]:
    """Mark a legacy message as read, returning 404 when it is missing."""
    if not mark_read(message_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Message {message_id} not found",
        )
    return {"status": "ok"}
