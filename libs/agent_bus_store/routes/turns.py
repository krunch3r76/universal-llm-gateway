"""Turn routes - CRUD for individual turns within threads."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..auth import require_token
from ..db import (
    TurnAlreadyAcknowledged,
    UnreadTurnsExist,
    delete_turn,
    get_turn_by_number,
    get_turns,
    insert_turn,
    mark_turn_read,
    normalize_thread_id,
    update_turn,
    update_turn_status,
)
from ..models import AgentName
from ..turns_models import (
    Attachment,
    Turn,
    TurnCreate,
    TurnCreated,
    TurnList,
    TurnStatus,
    TurnStatusUpdate,
    TurnUpdate,
)

router = APIRouter(dependencies=[Depends(require_token)])


def _turn_from_row(r: dict[str, Any]) -> Turn:
    """Map one database turn row into the API model with alias fields."""
    raw_attachments = r.get("attachments")
    attachments = (
        [Attachment(**a) for a in raw_attachments] if raw_attachments else None
    )
    return Turn.model_validate(
        {
            "id": r["id"],
            "thread": r["thread"],
            "turn_number": r["turn_number"],
            "from": r["from_agent"],
            "to": r["to_agent"],
            "subject": r["subject"],
            "body": r.get("body"),
            "status": r["status"],
            "supersedes_turn": r["supersedes_turn"],
            "created_at": r["created_at"],
            "read_at": r["read_at"],
            "attachments": attachments,
        }
    )


@router.post(
    "/turns",
    status_code=status.HTTP_201_CREATED,
    response_model=TurnCreated,
)
async def create_turn(turn: TurnCreate) -> TurnCreated:
    """Create one turn, enforcing unread and status invariants from storage logic."""
    turn.thread = normalize_thread_id(turn.thread)
    att_dicts = [a.model_dump() for a in turn.attachments] if turn.attachments else None
    try:
        turn_id, ts, turn_number = insert_turn(
            thread=turn.thread,
            from_agent=turn.from_agent,
            to_agent=turn.to,
            subject=turn.subject,
            body=turn.body,
            status=turn.status,
            thread_slug=turn.thread_slug,
            after_turn=turn.after_turn,
            supersedes_turn=turn.supersedes_turn,
            attachments=att_dicts,
        )
    except UnreadTurnsExist as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "unread_turns_exist",
                "message": "Read all turns addressed to you before posting",
                "unread_turns": e.unread,
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return TurnCreated(
        id=turn_id,
        thread=turn.thread,
        turn_number=turn_number,
        created_at=datetime.fromisoformat(ts),
    )


@router.get(
    "/turns",
    response_model=TurnList,
)
async def list_turns(
    thread: str | None = Query(None),
    to: AgentName | None = Query(None),
    unread: bool = Query(False),
    turn_status: TurnStatus | None = Query(None, alias="status"),
    last: int | None = Query(None),
    compact: bool = Query(False),
    mark_read_flag: bool = Query(False, alias="mark_read"),
    include_superseded: bool = Query(False),
) -> TurnList:
    """List turns with optional filters and optional mark-read side effects."""
    if thread is not None:
        thread = normalize_thread_id(thread)
    rows = get_turns(
        thread=thread,
        to=to,
        unread=unread,
        status=turn_status,
        last=last,
        compact=compact,
        mark_read=mark_read_flag,
        include_superseded=include_superseded,
    )
    return TurnList(turns=[_turn_from_row(r) for r in rows])


@router.patch("/turns/{turn_id}/read")
async def mark_turn_read_route(turn_id: int) -> dict[str, str]:
    """Mark a turn read and return the persisted read timestamp."""
    read_at = mark_turn_read(turn_id)
    if read_at is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Turn {turn_id} not found",
        )
    return {"status": "ok", "read_at": read_at}


@router.patch(
    "/turns/{turn_id}",
    response_model=Turn,
)
async def update_turn_route(turn_id: int, body: TurnUpdate) -> Turn:
    """Update mutable turn fields while rejecting already acknowledged turns."""
    if body.subject is None and body.body is None and body.append is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one of subject, body, or append required",
        )
    try:
        row = update_turn(
            turn_id,
            subject=body.subject,
            body=body.body,
            append=body.append,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except TurnAlreadyAcknowledged:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Turn already acknowledged - cannot modify",
        )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Turn {turn_id} not found",
        )
    return _turn_from_row(row)


@router.patch("/turns/{turn_id}/status")
async def update_turn_status_route(
    turn_id: int, body: TurnStatusUpdate
) -> dict[str, str]:
    """Set a turn status, including supersede linkage validation semantics."""
    if body.status == TurnStatus.SUPERSEDED and body.supersedes_turn is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="supersedes_turn required when status is 'superseded'",
        )
    if body.status != TurnStatus.SUPERSEDED and body.supersedes_turn is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="supersedes_turn must be null when status is not 'superseded'",
        )
    if not update_turn_status(
        turn_id, status=body.status, supersedes_turn=body.supersedes_turn
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Turn {turn_id} not found",
        )
    return {"status": "ok"}


@router.delete("/turns/{turn_id}")
async def delete_turn_route(
    turn_id: int,
    force: bool = Query(False),
) -> dict[str, Any]:
    """Delete a turn, respecting read-protection unless force is explicitly set."""
    try:
        return delete_turn(turn_id, force=force)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Turn {turn_id} not found",
        )
    except TurnAlreadyAcknowledged:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Turn already acknowledged - use force=true to delete",
        )


@router.get(
    "/turns/by-number",
    response_model=Turn,
)
async def get_turn_by_number_route(
    thread: str = Query(...),
    turn_number: int = Query(...),
) -> Turn:
    """Look up a single turn by thread + turn_number directly."""
    thread = normalize_thread_id(thread)
    row = get_turn_by_number(thread, turn_number)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Turn {turn_number} not found in thread {thread}",
        )
    return _turn_from_row(row)
