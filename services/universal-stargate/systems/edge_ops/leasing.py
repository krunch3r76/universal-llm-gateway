"""Edge-facing edge-op service functions: lease, events, result, release.

Every one of these runs because the edge called a Stargate resource on its own
connection. Stargate holds the lease and directive requests open for up to 60 s
and then answers empty, so the edge re-issues; it never dials the edge, and
there is no heartbeat routine — each posted event renews the lease.

The waits here are idle bounds on the absence of progress, not completion
deadlines: a lease long-poll that finds nothing returns nothing, and the edge
asks again.
"""

from __future__ import annotations

import time
from typing import Any

from . import config
from .model import (
    CODE_LEASE_MISMATCH,
    EdgeOpRefusedError,
    LeaseGrant,
    Outcome,
)
from .store import EdgeOpsJournal
from .waiter import QUEUED_CHANNEL, TRANSITION_CHANNEL


async def lease(
    journal: EdgeOpsJournal,
    *,
    edge_id: str,
    kinds: tuple[str, ...],
    wait_s: float | None = None,
) -> LeaseGrant | None:
    """Long-poll for the next queued op this edge serves.

    Records the edge's presence first — that stamp is what lets ``admit`` fail
    fast with ``edge_unavailable`` — then claims the oldest queued op of a
    served kind, parking on the queue channel until one arrives or the hold
    elapses. ``None`` means the hold elapsed with nothing to do and the edge
    should re-issue the request.
    """
    budget = config.clamp_long_poll_seconds(wait_s)
    await journal.record_poll(edge_id=edge_id, kinds=kinds)
    hold_until = time.monotonic() + budget
    while True:
        since = journal.waiter.epoch(QUEUED_CHANNEL)
        grant = await journal.claim_lease(edge_id=edge_id, kinds=kinds)
        if grant is not None:
            return grant
        remaining = hold_until - time.monotonic()
        if remaining <= 0:
            return None
        await journal.waiter.wait(QUEUED_CHANNEL, since=since, timeout=remaining)


async def post_event(
    journal: EdgeOpsJournal,
    *,
    op_id: str,
    edge_id: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record one open-typed edge event and renew the lease.

    The event type is a free string and the payload free JSON. The single
    reserved type is ``started``: it moves a leased op to ``started``, and its
    2xx is what a ``never`` kind waits for before running its handler, so a
    lease lost before that ack can be re-leased without duplicating work.

    Raises:
        EdgeOpRefusedError: 409 when this edge does not hold the op's lease.
    """
    outcome = await journal.post_event(
        op_id=op_id,
        edge_id=edge_id,
        event_type=event_type,
        payload=payload or {},
    )
    if outcome is None:
        raise _lease_mismatch(op_id, edge_id)
    state, seq = outcome
    return {"op_id": op_id, "status": state, "seq": seq}


async def post_result(
    journal: EdgeOpsJournal,
    *,
    op_id: str,
    edge_id: str,
    result: Any,
    outcome: Outcome = "completed",
) -> dict[str, Any]:
    """Store the handler result verbatim and close the op.

    The substrate does not read the result: ``outcome="completed"`` is the
    terminal state even when the handler answered ``{"ok": false}``, and
    ``outcome="failed"`` is for an exception or an infrastructure error. An op
    that is already terminal keeps its stored result; it is never re-run.

    Raises:
        EdgeOpRefusedError: 409 when this edge does not hold the op's lease.
    """
    state = await journal.post_result(
        op_id=op_id,
        edge_id=edge_id,
        outcome=outcome,
        result=result,
    )
    if state is None:
        raise _lease_mismatch(op_id, edge_id)
    return {"op_id": op_id, "status": state}


async def release(
    journal: EdgeOpsJournal,
    *,
    op_id: str,
    edge_id: str,
) -> dict[str, Any]:
    """Give a lease back without a result.

    A still-leased op returns to the queue. A started op cannot be re-run, so
    releasing it records ``unknown``.

    Raises:
        EdgeOpRefusedError: 409 when this edge does not hold the op's lease.
    """
    state = await journal.release(op_id=op_id, edge_id=edge_id)
    if state is None:
        raise _lease_mismatch(op_id, edge_id)
    return {"op_id": op_id, "status": state}


async def directives(
    journal: EdgeOpsJournal,
    *,
    op_id: str,
    after: int = 0,
    wait_s: float | None = None,
) -> list[dict[str, Any]]:
    """Long-poll for server-to-edge directives above sequence *after*.

    This is how Stargate reaches the edge without opening a connection to it:
    the edge holds one of these open per live op and Stargate answers into it.
    A1 ships no directive producer, so the list is empty until the cancel and
    nudge writers land; the read itself is already against the authority store.
    """
    budget = config.clamp_long_poll_seconds(wait_s)
    hold_until = time.monotonic() + budget
    while True:
        since = journal.waiter.epoch(TRANSITION_CHANNEL)
        pending = await journal.directives_after(op_id=op_id, after=after)
        if pending:
            return pending
        remaining = hold_until - time.monotonic()
        if remaining <= 0:
            return []
        await journal.waiter.wait(TRANSITION_CHANNEL, since=since, timeout=remaining)


def _lease_mismatch(op_id: str, edge_id: str) -> EdgeOpRefusedError:
    """Build the refusal for a write by an edge that holds no lease."""
    return EdgeOpRefusedError(
        409,
        CODE_LEASE_MISMATCH,
        f"Edge {edge_id!r} does not hold the lease for op {op_id!r}.",
    )
