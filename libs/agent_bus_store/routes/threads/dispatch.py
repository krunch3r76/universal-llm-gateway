"""Dispatch lifecycle routes: admit, claim-and-post, terminate, link lookup."""

from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException, status

from ...db import (
    PendingShellContention,
    admit_dispatch,
    claim_and_post_turn,
    get_dispatch_link_by_execution_id,
    normalize_thread_id,
    terminate_dispatch,
)
from ...turns_models import (
    DispatchAdmit,
    DispatchClaimAndPost,
    DispatchLinkByExecution,
    DispatchTerminate,
    ThreadDetail,
)
from . import router
from .detail import _thread_detail


@router.get(
    "/dispatch-links/{execution_id}",
    response_model=DispatchLinkByExecution,
)
async def get_dispatch_link_route(execution_id: str) -> DispatchLinkByExecution:
    """Resolve execution_id to its durable dispatch link row."""
    row = get_dispatch_link_by_execution_id(execution_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dispatch link for execution_id {execution_id!r} not found",
        )
    terminal_at = row.get("terminal_at")
    return DispatchLinkByExecution(
        thread_id=row["thread_id"],
        pipeline_id=row["pipeline_id"],
        terminal_status=row.get("terminal_status"),
        terminal_at=(
            datetime.fromisoformat(terminal_at) if terminal_at is not None else None
        ),
    )


@router.post(
    "/threads/{thread_id}/dispatch-admit",
    response_model=ThreadDetail,
)
async def dispatch_admit_route(thread_id: str, body: DispatchAdmit) -> ThreadDetail:
    """Register a pipeline dispatch link and advance lifecycle state.

    - If bus_lifecycle_state == "pending": transitions to "admitted".
    - If bus_lifecycle_state is NULL: link registered, no lifecycle transition
      (documented coverage gap — pre-create with lifecycle_state="pending" for
      full recovery support).
    - Returns 409 when thread is in a terminal state.
    """
    thread_id = normalize_thread_id(thread_id)
    try:
        row = admit_dispatch(
            thread_id=thread_id,
            execution_id=body.execution_id,
            pipeline_id=body.pipeline_id,
            caller_agent=body.caller_agent,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found",
        )
    return _thread_detail(row)


@router.post(
    "/threads/{thread_id}/dispatch-claim-and-post",
    response_model=ThreadDetail,
)
async def dispatch_claim_and_post_route(
    thread_id: str, body: DispatchClaimAndPost
) -> ThreadDetail:
    """Atomically claim a pending-empty shell and post the first pointer turn.

    Checks pending+turn_count==0, admits, inserts the pointer turn, and
    transitions admitted->active in one SQLite write transaction.

    Returns 409 with code=pending_shell_contention when the CAS guard fails.
    """
    thread_id = normalize_thread_id(thread_id)
    try:
        row = claim_and_post_turn(
            thread_id=thread_id,
            execution_id=body.execution_id,
            pipeline_id=body.pipeline_id,
            caller_agent=body.caller_agent,
            from_agent=body.from_agent,
            to_agent=body.to_agent,
            subject=body.subject,
            body=body.body,
        )
    except PendingShellContention as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "pending_shell_contention", "message": str(exc)},
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found",
        )
    return _thread_detail(row)


@router.post(
    "/threads/{thread_id}/dispatch-terminate",
    response_model=ThreadDetail,
)
async def dispatch_terminate_route(
    thread_id: str, body: DispatchTerminate
) -> ThreadDetail:
    """Mark dispatch link terminal_status (completed or failed)."""
    from agent_bus_store.disposition import maybe_auto_close_after_dispatch_terminate

    thread_id = normalize_thread_id(thread_id)
    row = terminate_dispatch(
        thread_id=thread_id,
        terminal_status=body.terminal_status,
        execution_id=body.execution_id,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found",
        )
    closed = maybe_auto_close_after_dispatch_terminate(
        thread_id,
        terminal_status=body.terminal_status,
        explicit_bus_lifecycle=body.bus_lifecycle,
    )
    if closed is not None:
        row = closed
    return _thread_detail(row)
