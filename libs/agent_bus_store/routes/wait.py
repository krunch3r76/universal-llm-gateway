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
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from openapi_mcp.binding import x_mcp

from ..auth import require_token
from ..db import (
    get_thread,
    get_thread_turns_asc,
    get_thread_with_links,
    normalize_thread_id,
)
from ..producer_projection import project_thread_producers
from ..sdk_liveness import reader_liveness_witness_async
from ..wait_status import (
    DEAD_WAIT_DETAIL,
    DEAD_WAIT_ERROR,
    STATUS_COMPLETION_MODES,
    Completion,
    build_suggested_next,
    classify_producer_link,
    derive_status,
    is_dead_wait_no_auto_producer,
    qualifying_reply_for_wait,
    qualifying_status_turn,
    wait_predicate_satisfied,
)

router = APIRouter(dependencies=[Depends(require_token)])

# Life / Cowork MCP client continuous-hold ceiling for agent_bus.wait is 60s
# (Fable live observation 2026-08-03 on toys connector — "Client ceiling here
# is 60s, not 300"). The 2026-08-02 raise to 300 contradicted the client and
# forced seats to re-arm after false 300s budgets. constraint:mcp-client-300s-ceiling
# remains a separate historical SSE/tools ceiling — do not conflate with wait.
MAX_WAIT_SECONDS = 60.0
_POLL_INTERVAL_SECONDS = 1.0
_GENERATE_PIPELINE_ID = "cursor-sdk-generate"
_STATUS_DONE_UNSATISFIABLE = "cursor_sdk_status_done_unsatisfiable"
_STATUS_DONE_FIX_HINT = "completion=first_reply_from from_agent=cursor-sdk"


async def _pinned_reader_witness(
    *,
    thread_id: str,
    execution_id: str | None,
    dispatch_links: list[dict[str, Any]],
    now: datetime,
) -> tuple[
    Literal["live", "dead"] | None,
    dict[str, Literal["live", "dead"] | None] | None,
]:
    """Probe only the pinned execution_id, at most once, for this snapshot.

    A pinned snapshot does not list those other links. Probing every past-grace
    link made ``wait`` cost grow with N, one GIW call each, using the probe's
    existing timeout. Awaits the async GIW probe so a hung peer cannot pin the
    uvicorn thread. Does not write ``terminal_status``.
    """
    if not execution_id:
        return None, None
    row = next(
        (link for link in dispatch_links if link.get("execution_id") == execution_id),
        None,
    )
    if row is None or row.get("terminal_status"):
        return None, None
    witness = await reader_liveness_witness_async(
        thread_id=thread_id,
        execution_id=execution_id,
        linked_at=row.get("linked_at"),
        now=now,
        pipeline_id=row.get("pipeline_id"),
    )
    return witness, {execution_id: witness}


async def _snapshot(
    thread_id: str,
    *,
    after_turn: int,
    completion: Completion,
    execution_id: str | None = None,
) -> dict[str, Any]:
    """Read thread + turns once and build the wait response payload.

    Exposes raw observables (pointer_read_at, qualifying_reply_turn) as DATA so
    a caller can inspect them, but never promotes them to a lifecycle ``status``
    (decision: ship C — no read_at-derived awaiting_push).
    """
    thread_row = get_thread_with_links(thread_id)
    if thread_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found",
        )
    dispatch_links = thread_row.get("dispatch_links") or []
    turns = get_thread_turns_asc(thread_id)
    pointer = next((t for t in turns if t["turn_number"] == after_turn), None)
    clock = datetime.now(UTC)
    pinned_witness, witness_map = await _pinned_reader_witness(
        thread_id=thread_id,
        execution_id=execution_id,
        dispatch_links=dispatch_links,
        now=clock,
    )
    producer = classify_producer_link(
        execution_id=execution_id,
        dispatch_links=dispatch_links,
        now=clock,
        liveness_witness=pinned_witness,
    )
    reply = qualifying_reply_for_wait(
        turns,
        after_turn=after_turn,
        completion=completion,
        execution_id=execution_id,
        producer=producer,
    )
    reply_turn = reply["turn_number"] if reply else None
    complete = wait_predicate_satisfied(
        thread_row,
        turns,
        after_turn=after_turn,
        completion=completion,
        execution_id=execution_id,
        producer=producer,
    )
    wait_status = derive_status(
        thread_row,
        turns,
        after_turn=after_turn,
        completion=completion,
        execution_id=execution_id,
        dispatch_links=dispatch_links,
        producer=producer,
    )
    suggested = build_suggested_next(
        thread_row,
        complete=complete,
        completion=completion,
        qualifying_reply_turn=reply_turn,
        after_turn=after_turn,
        turns=turns,
    )
    # A pinned probe asks for one producer. Listing every dispatch link
    # (~70 rows, ~14 KB on role:root 16416) tripped KERNEL-H (a:39827).
    # The thread-wide registry remains the unpinned wait.
    if execution_id:
        producers = [producer]
    else:
        producers = project_thread_producers(
            dispatch_links,
            now=clock,
            liveness_witnesses=witness_map,
        )
    return {
        "thread_id": thread_id,
        "complete": complete,
        "status": wait_status,
        "producer": producer,
        "producers": producers,
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


def _cursor_sdk_generate_only(thread_row: dict[str, Any]) -> bool:
    """True when no producer on the thread can post a status:done subject.

    Refuse only when every dispatch_links row is pipeline_id
    cursor-sdk-generate, or the thread carries that tag and has no links.
    Any other pipeline falls through. An empty link list without the tag is
    not treated as pure: vacuous all() would refuse Auto waits that have
    no dispatch row.
    """
    links = list(thread_row.get("dispatch_links") or [])
    if not links:
        tags = thread_row.get("tags") or []
        return _GENERATE_PIPELINE_ID in tags
    return all(link.get("pipeline_id") == _GENERATE_PIPELINE_ID for link in links)


def _raise_if_status_done_unsatisfiable(
    thread_id: str,
    turns: list[dict[str, Any]],
    *,
    after_turn: int,
    completion: str,
) -> None:
    """422 status:done on a pure cursor-sdk-generate thread before the poll.

    The generate closeout subject never carries the status: prefix, so
    sleeping out the wait budget cannot succeed. The existing subject-prefix
    matcher runs once over turns after after_turn first: a hit means the
    predicate is already met and the caller returns the normal snapshot.
    """
    if completion != "status:done":
        return
    if (
        qualifying_status_turn(turns, after_turn=after_turn, status_token="status:done")
        is not None
    ):
        return
    thread_row = get_thread_with_links(thread_id)
    if thread_row is None or not _cursor_sdk_generate_only(thread_row):
        return
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={
            "error": _STATUS_DONE_UNSATISFIABLE,
            "fix_hint": _STATUS_DONE_FIX_HINT,
        },
    )


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
    execution_id: str | None = Query(None),
) -> dict[str, Any]:
    """Bounded server-side wait. ``wait=0`` is an immediate snapshot.

    ``completion`` ∈ {first_reply_from, proof_reply_from, thread_closed,
    status:done, status:failed, status:needs-attended, status:superseded}.
    ``from_agent`` is required for first_reply_from and proof_reply_from.
    when no turn exists after ``after_turn``, else ``predicate_unmet`` (turns
    advanced, predicate still false). Push state is not inferred from read_at.

    ``status:done`` on a pure cursor-sdk-generate producer (every dispatch
    link is that pipeline, or the tag and no links) returns 422
    ``cursor_sdk_status_done_unsatisfiable`` with ``fix_hint`` naming
    ``first_reply_from``, unless a later turn already matches the subject
    prefix. A thread with any other producer keeps the normal poll.
    """
    thread_id = normalize_thread_id(thread_id)
    allowed = (
        "first_reply_from",
        "proof_reply_from",
        "thread_closed",
        *sorted(STATUS_COMPLETION_MODES),
    )
    if completion not in allowed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"unknown completion mode {completion!r}; "
                "expected first_reply_from | proof_reply_from | thread_closed | "
                "status:done | status:failed | status:needs-attended | "
                "status:superseded"
            ),
        )
    if completion in {"first_reply_from", "proof_reply_from"} and not from_agent:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"completion={completion} requires from_agent",
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
    if is_dead_wait_no_auto_producer(turns, after_turn=after_turn, completion=comp):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": DEAD_WAIT_ERROR,
                "message": DEAD_WAIT_DETAIL,
                "pointer_turn": after_turn,
            },
        )
    _raise_if_status_done_unsatisfiable(
        thread_id,
        turns,
        after_turn=after_turn,
        completion=completion,
    )

    wait_clamped = max(0.0, min(wait, MAX_WAIT_SECONDS))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_clamped

    while True:
        snap = await _snapshot(
            thread_id,
            after_turn=after_turn,
            completion=comp,
            execution_id=execution_id,
        )
        if snap["complete"] or loop.time() >= deadline:
            return snap
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
