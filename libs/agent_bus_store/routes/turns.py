"""Turn routes - CRUD for individual turns within threads."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..auth import require_token
from ..db import (
    TurnAlreadyAcknowledged,
    UnreadTurnsExist,
    bulk_mark_read_state,
    delete_turn,
    get_latest_turn,
    get_thread_turn_count,
    get_turn_by_number,
    get_turns,
    get_unread_thread_toc,
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
    TurnReadStateBulk,
    TurnReadStateBulkResult,
    TurnStatus,
    TurnStatusUpdate,
    TurnUpdate,
    UnreadThreadToc,
    UnreadThreadTocRow,
    active_since_window_label,
    parse_active_since,
    turn_body_limit_error,
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
    if error_detail := turn_body_limit_error(
        turn.body,
        allow_long_body=turn.allow_long_body,
    ):
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=error_detail,
        )
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
            detail=e.to_detail(),
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    try:
        import asyncio

        loop = asyncio.get_running_loop()
        loop.run_in_executor(
            None,
            lambda: _maybe_trigger_implement_closeout(
                thread=turn.thread, body=turn.body
            ),
        )
    except Exception:
        pass
    try:
        from agent_bus_store.disposition import (
            maybe_auto_close_after_implement_handoff_reply,
        )

        maybe_auto_close_after_implement_handoff_reply(
            turn.thread,
            turn_number=turn_number,
            from_agent=turn.from_agent,
        )
    except Exception:
        from universal_logging import get_logger

        get_logger(__name__).warning(
            "auto-close after implement reply failed: thread=%s turn=%s",
            turn.thread,
            turn_number,
            exc_info=True,
        )
    return TurnCreated(
        id=turn_id,
        thread=turn.thread,
        turn_number=turn_number,
        created_at=datetime.fromisoformat(ts),
    )


def _maybe_trigger_implement_closeout(*, thread: str, body: str | None) -> None:
    from agent_bus_store.closeout_trigger import maybe_trigger_closeout

    maybe_trigger_closeout(thread=thread, body=body)


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


@router.get(
    "/turns/unread-toc",
    response_model=UnreadThreadToc,
)
async def list_unread_thread_toc(
    to: AgentName = Query(...),
    mark_read_flag: bool = Query(False, alias="mark_read"),
    active_since: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    all_flag: bool = Query(False, alias="all"),
) -> UnreadThreadToc:
    """Recipient-scoped unread inbox digest: enriched, windowed, bounded."""
    try:
        cutoff = None if all_flag else parse_active_since(active_since)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_active_since",
                "message": f"Invalid active_since value: {active_since!r}",
            },
        )
    rows, marked, total_threads, total_turns, truncated = get_unread_thread_toc(
        to=to,
        mark_read=mark_read_flag,
        active_since=cutoff,
        limit=limit,
        all_threads=all_flag,
    )
    toc_rows = [
        UnreadThreadTocRow(
            thread=r["thread"],
            unread_count=r["unread_count"],
            latest_turn_number=r["latest_turn_number"],
            slug=r["slug"],
            last_subject=r.get("last_subject"),
            last_activity_at=datetime.fromisoformat(r["last_activity_at"]),
        )
        for r in rows
    ]
    window_label = active_since if active_since is not None else None
    if window_label is None and not all_flag:
        window_label = active_since_window_label(None)
    return UnreadThreadToc(
        threads=toc_rows,
        total_unread_threads=total_threads,
        total_unread_turns=total_turns,
        marked_read=marked,
        truncated=truncated,
        active_since=window_label,
    )


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
    except TurnAlreadyAcknowledged as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=e.to_detail(action="modify"),
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
    except TurnAlreadyAcknowledged as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=e.to_detail(action="delete"),
        )


@router.get(
    "/turns/by-number",
    response_model=Turn,
)
async def get_turn_by_number_route(
    thread: str = Query(...),
    turn_number: str = Query(...),
) -> Turn:
    """Look up a single turn by thread + turn_number (or ``latest``)."""
    thread = normalize_thread_id(thread)
    if turn_number == "latest":
        turn_count = get_thread_turn_count(thread)
        if turn_count == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "empty_thread",
                    "message": f"No turns in thread {thread}",
                    "data": {"turn_count": 0},
                },
            )
        row = get_latest_turn(thread)
    else:
        try:
            tn = int(turn_number)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_turn_number",
                    "message": f"turn_number must be int or 'latest', got {turn_number!r}",
                },
            )
        row = get_turn_by_number(thread, tn)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Turn {turn_number} not found in thread {thread}",
        )
    return _turn_from_row(row)


@router.patch(
    "/threads/{thread_id}/turns/read-state",
    response_model=TurnReadStateBulkResult,
)
async def bulk_mark_read_state_route(
    thread_id: str, body: TurnReadStateBulk
) -> TurnReadStateBulkResult:
    """Bulk mark read — ``turn_numbers[]`` XOR ``through_turn`` (+ ``agent``)."""
    has_list = body.turn_numbers is not None
    has_through = body.through_turn is not None
    if has_list == has_through:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "read_state_xor_violation",
                "message": "Provide exactly one of turn_numbers or through_turn",
            },
        )
    if has_through and body.agent is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "through_turn_requires_agent",
                "message": "through_turn requires agent",
            },
        )
    thread_id = normalize_thread_id(thread_id)
    try:
        marked = bulk_mark_read_state(
            thread=thread_id,
            turn_numbers=body.turn_numbers,
            through_turn=body.through_turn,
            agent=body.agent,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    return TurnReadStateBulkResult(marked_read=marked)
