"""Server-side short-blocking wait for handoff result handles.

GET /threads/{thread_id}/wait — bounded long-poll. Blocks server-side until the
completion predicate holds or ``wait`` seconds elapse, re-querying the thread
store (SoT) on a fixed interval. Mirrors the pipeline-result wait contract:
``wait`` clamped to <= MAX_WAIT_SECONDS; ``wait=0`` returns an immediate snapshot.

The MCP relay is a thin passthrough (one HTTP call) — all poll/derivation logic
lives here, never in the MCP handler.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from openapi_mcp.binding import x_mcp

from ..auth import require_token
from ..db import get_thread, get_thread_turns_asc, normalize_thread_id
from ..wait_status import (
    DEAD_WAIT_DETAIL,
    DEAD_WAIT_ERROR,
    STATUS_COMPLETION_MODES,
    Completion,
    build_suggested_next,
    derive_status,
    is_complete,
    is_dead_wait_no_auto_producer,
    qualifying_reply,
    qualifying_status_turn,
)

router = APIRouter(dependencies=[Depends(require_token)])

# Operator bind 2026-08-02 (agent-bus:6661): continuous Cowork wait up to the
# Anthropic MCP client hard ceiling (constraint:mcp-client-300s-ceiling / a:5129).
# Prior 60s was a self-imposed fleet clamp, not a substrate law.
MAX_WAIT_SECONDS = 300.0
_POLL_INTERVAL_SECONDS = 1.0


def _snapshot(
    thread_id: str, *, after_turn: int, completion: Completion
) -> dict[str, Any]:
    """Read thread + turns once and build the wait response payload.

    Exposes raw observables (pointer_read_at, qualifying_reply_turn) as DATA so
    a caller can inspect them, but never promotes them to a lifecycle ``status``
    (decision: ship C — no read_at-derived awaiting_push).
    """
    thread_row = get_thread(thread_id)
    if thread_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found",
        )
    turns = get_thread_turns_asc(thread_id)
    complete = is_complete(
        thread_row, turns, after_turn=after_turn, completion=completion
    )
    wait_status = derive_status(
        thread_row, turns, after_turn=after_turn, completion=completion
    )
    pointer = next((t for t in turns if t["turn_number"] == after_turn), None)
    mode = completion.get("mode", "first_reply_from")
    if mode in STATUS_COMPLETION_MODES:
        reply = qualifying_status_turn(
            turns, after_turn=after_turn, status_token=str(mode)
        )
    else:
        reply = qualifying_reply(
            turns, after_turn=after_turn, from_agent=completion.get("from_agent")
        )
    reply_turn = reply["turn_number"] if reply else None
    suggested = build_suggested_next(
        thread_row,
        complete=complete,
        completion=completion,
        qualifying_reply_turn=reply_turn,
        after_turn=after_turn,
        turns=turns,
    )
    return {
        "thread_id": thread_id,
        "complete": complete,
        "status": wait_status,
        "suggested_next": suggested,
        # push_required is never asserted under C (no observable push signal).
        # The field is retained at constant False for forward-compat with a
        # future Phase 4 server-owned ack; do NOT derive it from read_at.
        "push_required": False,
        "next_poll_after_s": 0 if complete else int(_POLL_INTERVAL_SECONDS),
        "turn_count": len(turns),
        "thread_status": thread_row["status"],
        # Raw, non-authoritative observables (telemetry only — not a status).
        "pointer_read_at": pointer.get("read_at") if pointer else None,
        "qualifying_reply_turn": reply_turn,
    }


@router.get(
    "/threads/{thread_id}/wait",
    openapi_extra=x_mcp("wait", tool="agent_bus"),
)
async def wait_thread_route(
    thread_id: str,
    after_turn: int = Query(1, ge=0),
    wait: float = Query(0.0, ge=0.0),
    completion: str = Query("first_reply_from"),
    from_agent: str | None = Query(None),
) -> dict[str, Any]:
    """Bounded server-side wait. ``wait=0`` is an immediate snapshot.

    ``completion`` ∈ {first_reply_from, thread_closed, status:done,
    status:failed, status:needs-attended}. ``from_agent`` is required for
    first_reply_from. Pre-reply status is always ``awaiting_first_reply`` (C)
    — push state is not inferred from read_at.
    """
    thread_id = normalize_thread_id(thread_id)
    allowed = ("first_reply_from", "thread_closed", *sorted(STATUS_COMPLETION_MODES))
    if completion not in allowed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"unknown completion mode {completion!r}; "
                "expected first_reply_from | thread_closed | "
                "status:done | status:failed | status:needs-attended"
            ),
        )
    if completion == "first_reply_from" and not from_agent:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="completion=first_reply_from requires from_agent",
        )

    comp: Completion = {"mode": completion}  # type: ignore[typeddict-item]
    if from_agent:
        comp["from_agent"] = from_agent

    if get_thread(thread_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found",
        )
    turns = get_thread_turns_asc(thread_id)
    if is_dead_wait_no_auto_producer(
        turns, after_turn=after_turn, completion=comp
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": DEAD_WAIT_ERROR,
                "message": DEAD_WAIT_DETAIL,
                "pointer_turn": after_turn,
            },
        )

    wait_clamped = max(0.0, min(wait, MAX_WAIT_SECONDS))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_clamped

    while True:
        snap = _snapshot(thread_id, after_turn=after_turn, completion=comp)
        if snap["complete"] or loop.time() >= deadline:
            return snap
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
