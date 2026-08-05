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
import json
import os
import uuid
from collections.abc import Callable
from typing import Literal

from universal_concurrency import CrossLaneTransferError, FifoCapacityGate, TransferHolderError

from services.git_integration_worker.cursor_sdk_capacity_invariant import (
    active_by_lane_counts,
    evaluate_i1,
)
from services.git_integration_worker.cursor_sdk_workspace import (
    default_write_path_is_lane_a,
    write_lease_slots,
)

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
_LANE_GATES: dict[GateLane, FifoCapacityGate] = {
    "standard": _STANDARD_GATE,
    "operator": _OPERATOR_GATE,
}


class SdkSlotAcquireStallError(TimeoutError):
    """Capacity acquire timed out due to unreleasable or cross-lane gate holders."""

    def __init__(
        self,
        *,
        dispatch_id: str,
        lane: GateLane,
        waited_s: float,
        gate_stats: dict[str, int | dict[str, int]],
        misplaced_holders: list[dict[str, str]],
    ) -> None:
        self.dispatch_id = dispatch_id
        self.lane = lane
        self.waited_s = waited_s
        self.gate_stats = gate_stats
        self.misplaced_holders = misplaced_holders
        detail = (
            f"cursor-sdk slot acquire stalled on {lane} lane after {waited_s:.0f}s; "
            f"misplaced_holders={misplaced_holders!r}"
        )
        super().__init__(detail)


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


def _direct_sdk_dispatch_lane(
    *,
    caller_agent: str | None = None,
    dispatch_id: str | None = None,
) -> GateLane:
    """Resolve lane from dispatch id / caller_agent only (no nest inheritance)."""
    if is_operator_sdk_dispatch(caller_agent=caller_agent, dispatch_id=dispatch_id):
        return "operator"
    return "standard"


def _read_nest_under(dispatch_id: str) -> str | None:
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    with CursorDispatchLedger.instance()._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    if row is None or not row["record_json"]:
        return None
    try:
        data = json.loads(row["record_json"])
    except json.JSONDecodeError:
        return None
    parent = data.get("nest_under") if isinstance(data, dict) else None
    return str(parent) if parent else None


def sdk_dispatch_lane(
    *,
    caller_agent: str | None = None,
    dispatch_id: str | None = None,
) -> GateLane:
    """Resolve which capacity lane owns a dispatch.

    Nested dispatches inherit the parent's lane so park/restore never crosses gates.
    """
    if dispatch_id:
        parent_id = _read_nest_under(dispatch_id)
        if parent_id:
            return sdk_dispatch_lane(
                dispatch_id=parent_id,
                caller_agent=_caller_agent_for_dispatch(parent_id),
            )
    return _direct_sdk_dispatch_lane(
        caller_agent=caller_agent, dispatch_id=dispatch_id
    )


def _gate_for_lane(lane: GateLane) -> FifoCapacityGate:
    return _LANE_GATES[lane]


def _gate_holding_holder(holder_id: str) -> tuple[FifoCapacityGate, GateLane] | None:
    """Return the gate that currently holds ``holder_id``, if any."""
    for lane, gate in _LANE_GATES.items():
        if holder_id in gate.holders:
            return gate, lane
    return None


def _misplaced_holders(*, gate_lane: GateLane) -> list[dict[str, str]]:
    """Holders on ``gate_lane`` whose direct lane resolution differs (phantoms)."""
    gate = _LANE_GATES[gate_lane]
    misplaced: list[dict[str, str]] = []
    for holder_id in gate.holders:
        direct = _direct_sdk_dispatch_lane(
            dispatch_id=holder_id,
            caller_agent=_caller_agent_for_dispatch(holder_id),
        )
        if direct != gate_lane:
            misplaced.append(
                {
                    "holder_id": holder_id,
                    "gate_lane": gate_lane,
                    "direct_lane": direct,
                }
            )
    return misplaced


def _caller_agent_for_dispatch(dispatch_id: str) -> str | None:
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    return CursorDispatchLedger.instance().read_caller_agent(dispatch_id=dispatch_id)


def _gate_for_dispatch(
    dispatch_id: str, *, caller_agent: str | None = None
) -> FifoCapacityGate:
    """Resolve the owning lane gate, consulting the ledger when the caller is implicit.

    Release/transfer callers know only ``dispatch_id``. Resolving the lane from the
    id alone recognizes just the ``auto-`` prefix, so an operator-lane dispatch
    admitted via ``caller_agent`` would release against the standard gate and leak
    its operator slot permanently (limit 3 ⇒ the lane wedges after three IDE
    dispatches). The ledger's ``caller_agent`` is the same value acquire resolved on.
    """
    resolved_agent = caller_agent or _caller_agent_for_dispatch(dispatch_id)
    lane = sdk_dispatch_lane(caller_agent=resolved_agent, dispatch_id=dispatch_id)
    return _gate_for_lane(lane)


async def acquire_sdk_dispatch_slot(
    *,
    dispatch_id: str | None = None,
    caller_agent: str | None = None,
    timeout: float | None = None,
    on_wait: Callable[[], None] | None = None,
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
    lane = sdk_dispatch_lane(caller_agent=caller_agent, dispatch_id=req_id)
    gate = _gate_for_lane(lane)
    try:
        await gate.acquire(req_id, timeout=timeout, on_wait=on_wait)
    except TimeoutError:
        # Close the grant/cancel race: release() may have handed us the slot
        # and registered us as holder between the deadline and the raise.
        await gate.force_release(req_id)
        misplaced = _misplaced_holders(gate_lane=lane)
        if misplaced:
            await reclaim_cross_lane_phantom_holders()
            raise SdkSlotAcquireStallError(
                dispatch_id=req_id,
                lane=lane,
                waited_s=float(timeout or 0),
                gate_stats=sdk_dispatch_gate_stats(),
                misplaced_holders=misplaced,
            ) from None
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
    """Park/restore capacity handoff — no waiter wake; ``active_count`` unchanged.

    Uses the gate where ``from_id`` is actually held (not inferred from id prefix
    alone) and rejects transfers that would install ``to_id`` on a different lane.
    """
    held = _gate_holding_holder(from_id)
    if held is None:
        raise TransferHolderError(
            f"transfer_sdk_dispatch_slot: from_id={from_id!r} holds no capacity slot"
        )
    gate, from_gate_lane = held
    to_lane = sdk_dispatch_lane(
        dispatch_id=to_id,
        caller_agent=_caller_agent_for_dispatch(to_id),
    )
    if to_lane != from_gate_lane:
        raise CrossLaneTransferError(
            f"transfer_sdk_dispatch_slot {from_id!r}→{to_id!r}: holder on "
            f"{from_gate_lane} gate but to_id resolves to {to_lane} lane"
        )
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


def _active_by_lane() -> dict[str, int]:
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    with CursorDispatchLedger.instance()._connect() as conn:
        rows = conn.execute(
            "SELECT record_json, lease_key, source_repo FROM cursor_sdk_dispatches "
            "WHERE COALESCE(read_only,0)=0 AND status IN ('admitted','running')"
        ).fetchall()
    return active_by_lane_counts([dict(row) for row in rows])


def _write_capacity_fields(
    *,
    standard: dict[str, int],
    operator: dict[str, int],
    live_by_lane: dict[str, int],
) -> dict[str, int | str | dict[str, dict[str, int]]]:
    std_lim = int(standard["limit"])
    op_lim = int(operator["limit"])
    configured_headroom = std_lim + op_lim
    lane_a_slots = write_lease_slots("A", gate_limit=configured_headroom)
    lane_b_slots = write_lease_slots("B", gate_limit=configured_headroom)
    live_writers = int(live_by_lane.get("A", 0)) + int(live_by_lane.get("B", 0))
    write_capacity_detail: dict[str, dict[str, int]] = {
        "lane_a": {"slots": lane_a_slots},
        "lane_b": {"slots": lane_b_slots},
    }
    if default_write_path_is_lane_a():
        headroom = lane_a_slots
        write_capacity = min(configured_headroom, headroom)
    else:
        headroom = lane_b_slots
        write_capacity = configured_headroom
    return {
        "write_capacity": write_capacity,
        "configured_headroom": configured_headroom,
        "live_writers": live_writers,
        "capacity_disposition": evaluate_i1(std_lim, op_lim, headroom),
        "write_capacity_detail": write_capacity_detail,
    }


def sdk_dispatch_gate_stats(
    *, lane: GateLane | None = None
) -> dict[str, int | str | dict[str, int | dict[str, int]]]:
    """Return active/queued/limit counters for cursor-sdk capacity gates.

    ``lane=None`` (default) returns combined totals plus per-lane breakdown.
    """
    standard = _lane_stats(_STANDARD_GATE)
    operator = _lane_stats(_OPERATOR_GATE)
    if lane == "standard":
        return standard
    if lane == "operator":
        return operator
    capacity = _write_capacity_fields(
        standard=standard,
        operator=operator,
        live_by_lane=_active_by_lane(),
    )
    return {
        "active": int(standard["active"]) + int(operator["active"]),
        "queued": int(standard["queued"]) + int(operator["queued"]),
        "limit": int(standard["limit"]) + int(operator["limit"]),
        **capacity,
        "active_by_lane": _active_by_lane(),
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


def sdk_dispatch_gate_holder_detail() -> dict[str, list[str]]:
    """Per-lane holder ids for live probes (manage busy_status / active-work)."""
    return {
        "standard": sorted(_STANDARD_GATE.holders),
        "operator": sorted(_OPERATOR_GATE.holders),
    }


async def reclaim_cross_lane_phantom_holders() -> list[str]:
    """Force-release holders installed on a gate belonging to a different lane."""
    reclaimed: list[str] = []
    for gate_lane, gate in _LANE_GATES.items():
        for holder_id in list(gate.holders):
            direct = _direct_sdk_dispatch_lane(
                dispatch_id=holder_id,
                caller_agent=_caller_agent_for_dispatch(holder_id),
            )
            if direct != gate_lane:
                if await gate.force_release(holder_id):
                    reclaimed.append(holder_id)
    return reclaimed
