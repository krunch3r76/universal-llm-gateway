"""FIFO capacity gates for cursor-sdk dispatches.

Standard lane (charter/autonomous): default limit 1 via ``CURSOR_SDK_DISPATCH_CONCURRENCY``.
Operator lane (IDE lead + cursor-auto operator-proxy): default limit 3 via
``CURSOR_SDK_OPERATOR_DISPATCH_CONCURRENCY``.

The slot is a dispatch/thread-lifetime lease. A timed-out outer coroutine must
not release capacity while the non-cancellable worker thread is still running.
Nest park/restore uses ``transfer_holder`` so siblings cannot steal the slot.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Literal

from universal_concurrency import FifoCapacityGate

GateLane = Literal["standard", "operator"]


def _standard_limit() -> int:
    raw = os.environ.get("CURSOR_SDK_DISPATCH_CONCURRENCY", "1")
    return max(1, int(raw))


def _operator_limit() -> int:
    raw = os.environ.get("CURSOR_SDK_OPERATOR_DISPATCH_CONCURRENCY", "3")
    return max(1, int(raw))


_STANDARD_GATE = FifoCapacityGate(limit=_standard_limit, gate_id="cursor-sdk-dispatch")
_OPERATOR_GATE = FifoCapacityGate(
    limit=_operator_limit, gate_id="cursor-sdk-operator-dispatch"
)


def is_operator_sdk_dispatch(
    *,
    caller_agent: str | None = None,
    dispatch_id: str | None = None,
) -> bool:
    """True for Kaywan IDE dispatches and cursor-auto operator-proxy nested SDK."""
    if dispatch_id and dispatch_id.startswith("auto-"):
        return True
    agent = (caller_agent or "").strip()
    if not agent:
        return False
    from agent_seat.registry import resolve_capability_cell_from_bus_address

    cell = resolve_capability_cell_from_bus_address(agent)
    return cell is not None and cell[1] == "cursor"


def sdk_dispatch_lane(
    *,
    caller_agent: str | None = None,
    dispatch_id: str | None = None,
) -> GateLane:
    """Resolve which capacity lane owns a dispatch."""
    if is_operator_sdk_dispatch(caller_agent=caller_agent, dispatch_id=dispatch_id):
        return "operator"
    return "standard"


def _gate_for_lane(lane: GateLane) -> FifoCapacityGate:
    return _OPERATOR_GATE if lane == "operator" else _STANDARD_GATE


def _caller_agent_for_dispatch(dispatch_id: str) -> str | None:
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    return CursorDispatchLedger.instance().read_caller_agent(dispatch_id=dispatch_id)


def _gate_for_dispatch(
    dispatch_id: str, *, caller_agent: str | None = None
) -> FifoCapacityGate:
    lane = sdk_dispatch_lane(caller_agent=caller_agent, dispatch_id=dispatch_id)
    return _gate_for_lane(lane)


async def acquire_sdk_dispatch_slot(
    *,
    dispatch_id: str | None = None,
    caller_agent: str | None = None,
    timeout: float | None = None,
) -> str:
    """Acquire the cursor-sdk FIFO capacity slot for ``dispatch_id``.

    Idempotent when ``dispatch_id`` already holds (nest park transfer path).
    Returns the holder id used for a matching release/transfer.

    ``timeout`` bounds the wait and raises ``TimeoutError``. Callers on the
    dispatch path MUST pass one: a caller that has already been made the
    ledger's write-lease holder but cannot take the gate slot is in
    ledger/gate split-brain, and an unbounded wait there sits upstream of
    every watchdog — no heartbeat, no outer timeout, no reap (the pre-arm
    lease wedge, dispatch 38611b297c16-4a1462e7).
    """
    req_id = dispatch_id or str(uuid.uuid4())
    gate = _gate_for_dispatch(req_id, caller_agent=caller_agent)
    try:
        await gate.acquire(req_id, timeout=timeout)
    except TimeoutError:
        # Close the grant/cancel race: release() may have handed us the slot
        # and registered us as holder between the deadline and the raise.
        await gate.force_release(req_id)
        raise
    return req_id


async def release_sdk_dispatch_slot(*, dispatch_id: str) -> None:
    """Release the cursor-sdk capacity slot, waking the next FIFO waiter if any."""
    gate = _gate_for_dispatch(dispatch_id)
    await gate.release(dispatch_id)


def release_sdk_dispatch_slot_sync(
    loop: asyncio.AbstractEventLoop, *, dispatch_id: str
) -> None:
    """Release the slot from a worker thread via the owning event loop."""
    fut = asyncio.run_coroutine_threadsafe(
        release_sdk_dispatch_slot(dispatch_id=dispatch_id), loop
    )
    fut.result(timeout=30.0)


async def force_release_sdk_dispatch_slot(*, dispatch_id: str) -> bool:
    """Reclaim a holder slot without requiring a matching release from that holder.

    Idempotent: returns False when ``dispatch_id`` is not a current holder.
    """
    gate = _gate_for_dispatch(dispatch_id)
    return await gate.force_release(dispatch_id)


def force_release_sdk_dispatch_slot_sync(
    loop: asyncio.AbstractEventLoop, *, dispatch_id: str
) -> bool:
    """Thread-safe ``force_release_sdk_dispatch_slot`` via the owning event loop."""
    fut = asyncio.run_coroutine_threadsafe(
        force_release_sdk_dispatch_slot(dispatch_id=dispatch_id), loop
    )
    return fut.result(timeout=30.0)


async def transfer_sdk_dispatch_slot(*, from_id: str, to_id: str) -> None:
    """Park/restore capacity handoff — no waiter wake; ``active_count`` unchanged."""
    gate = _gate_for_dispatch(from_id)
    await gate.transfer_holder(from_id=from_id, to_id=to_id)


def transfer_sdk_dispatch_slot_sync(
    loop: asyncio.AbstractEventLoop, *, from_id: str, to_id: str
) -> None:
    """Thread-safe ``transfer_sdk_dispatch_slot`` via the owning event loop."""
    fut = asyncio.run_coroutine_threadsafe(
        transfer_sdk_dispatch_slot(from_id=from_id, to_id=to_id), loop
    )
    fut.result(timeout=30.0)


def _lane_stats(gate: FifoCapacityGate) -> dict[str, int]:
    return {
        "active": gate.active_count,
        "queued": gate.queue_length,
        "limit": gate.current_limit,
    }


def sdk_dispatch_gate_stats(*, lane: GateLane | None = None) -> dict[str, int | dict[str, int]]:
    """Return active/queued/limit counters for cursor-sdk capacity gates.

    ``lane=None`` (default) returns combined totals plus per-lane breakdown.
    """
    standard = _lane_stats(_STANDARD_GATE)
    operator = _lane_stats(_OPERATOR_GATE)
    if lane == "standard":
        return standard
    if lane == "operator":
        return operator
    return {
        "active": int(standard["active"]) + int(operator["active"]),
        "queued": int(standard["queued"]) + int(operator["queued"]),
        "limit": int(standard["limit"]) + int(operator["limit"]),
        "standard": standard,
        "operator": operator,
    }


def sdk_dispatch_gate_holders(*, lane: GateLane | None = None) -> frozenset[str]:
    """Return current capacity holder dispatch ids (tests and live probes)."""
    if lane == "standard":
        return _STANDARD_GATE.holders
    if lane == "operator":
        return _OPERATOR_GATE.holders
    return _STANDARD_GATE.holders | _OPERATOR_GATE.holders
