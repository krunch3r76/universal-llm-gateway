"""Edge-facing edge-op service functions: lease, events, result, release."""

from __future__ import annotations

import time
from typing import Any

from . import config
from .model import (
    CODE_LEASE_MISMATCH,
    CODE_LEASE_STALE,
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
    """Long-poll for the next queued op this edge serves."""
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
    lease_id: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record one open-typed edge event and renew the lease."""
    try:
        outcome = await journal.post_event(
            op_id=op_id,
            edge_id=edge_id,
            lease_id=lease_id,
            event_type=event_type,
            payload=payload or {},
        )
    except EdgeOpRefusedError:
        raise
    if outcome is None:
        raise _lease_mismatch(op_id, edge_id)
    state, seq = outcome
    return {"op_id": op_id, "status": state, "seq": seq}


async def post_result(
    journal: EdgeOpsJournal,
    *,
    op_id: str,
    edge_id: str,
    lease_id: str,
    result: Any,
    outcome: Outcome = "completed",
) -> dict[str, Any]:
    """Store the handler result verbatim and close the op."""
    try:
        state = await journal.post_result(
            op_id=op_id,
            edge_id=edge_id,
            lease_id=lease_id,
            outcome=outcome,
            result=result,
        )
    except EdgeOpRefusedError:
        raise
    if state is None:
        raise _lease_mismatch(op_id, edge_id)
    return {"op_id": op_id, "status": state}


async def release(
    journal: EdgeOpsJournal,
    *,
    op_id: str,
    edge_id: str,
    lease_id: str,
) -> dict[str, Any]:
    """Give a lease back without a result."""
    try:
        state = await journal.release(op_id=op_id, edge_id=edge_id, lease_id=lease_id)
    except EdgeOpRefusedError:
        raise
    if state is None:
        raise _lease_mismatch(op_id, edge_id)
    return {"op_id": op_id, "status": state}


async def directives(
    journal: EdgeOpsJournal,
    *,
    op_id: str,
    edge_id: str,
    lease_id: str,
    after: int = 0,
    wait_s: float | None = None,
) -> list[dict[str, Any]]:
    """Long-poll for server-to-edge directives above sequence *after*."""
    record = await journal.get(op_id)
    if record is None or record.leased_by != edge_id:
        raise _lease_mismatch(op_id, edge_id)
    if record.lease_id != lease_id:
        raise EdgeOpRefusedError(
            409,
            CODE_LEASE_STALE,
            f"Edge op {op_id!r}: lease {lease_id!r} is stale for edge {edge_id!r}.",
        )
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
