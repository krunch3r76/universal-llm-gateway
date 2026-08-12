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

from universal_concurrency import (
    CrossLaneTransferError,
    FifoCapacityGate,
    TransferHolderError,
)

from services.git_integration_worker.cursor_sdk_capacity_invariant import (
    active_by_lane_counts,
    evaluate_i1,
)
from services.git_integration_worker.cursor_sdk_workspace import (
    default_write_path_is_lane_a,
    isolated_write_headroom,
    write_lease_slots,
)

GateLane = Literal["standard", "operator"]

_last_i1_disposition: Literal["ok", "clamp"] | None = None
_last_limit_derived: tuple[int, int, int] | None = None


def _standard_limit() -> int:
    from services.git_integration_worker.cursor_sdk_lane_regime import (
        lane_b_regime_active,
    )

    if lane_b_regime_active():
        return isolated_write_headroom()
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
    sdk_dispatch_gate_stats()
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


def _file_i1_clamp_friction(
    *,
    configured_ceiling: int,
    clamped_limit: int,
    provisioner_headroom: int,
) -> int | None:
    try:
        from cortex_store.dispatch_ops.ops_assertions_friction import _op_friction
    except ImportError:
        return None
    note = (
        "cursor-sdk I1 clamp: configured ceiling "
        f"{configured_ceiling} exceeds provisioner headroom {provisioner_headroom}; "
        f"effective standard limit {clamped_limit}"
    )
    try:
        result = _op_friction(
            owner="service:git_integration_worker",
            category="boot_drift",
            note=note,
            agent="cursor-sdk-gate",
            actionable=False,
            actionable_false_reason="machine-recovery informational",
        )
    except Exception:  # noqa: BLE001 — friction must not block admits
        return None
    if "error" in result:
        return None
    item = result.get("item") or {}
    try:
        return int(item.get("id"))
    except (TypeError, ValueError):
        return None


def _maybe_emit_regime_on_derivation_events(
    *,
    configured_ceiling: int,
    mintable: int,
    derived_limit: int,
    disposition: Literal["ok", "clamp"],
) -> None:
    global _last_i1_disposition, _last_limit_derived

    from services.git_integration_worker.cursor_sdk_events import (
        emit_frontier_sdk_gate_i1_clamp_transition,
        emit_frontier_sdk_gate_limit_derived,
    )

    derived_tuple = (configured_ceiling, mintable, derived_limit)
    if derived_tuple != _last_limit_derived:
        emit_frontier_sdk_gate_limit_derived(
            derived_limit=derived_limit,
            ceiling=configured_ceiling,
            provisioner_headroom=mintable,
            regime_on=True,
        )
        _last_limit_derived = derived_tuple

    if disposition != _last_i1_disposition and _last_i1_disposition is not None:
        friction_id: int | None = None
        if disposition == "clamp":
            friction_id = _file_i1_clamp_friction(
                configured_ceiling=configured_ceiling,
                clamped_limit=derived_limit,
                provisioner_headroom=mintable,
            )
        emit_frontier_sdk_gate_i1_clamp_transition(
            from_disposition=_last_i1_disposition,
            to_disposition=disposition,
            configured_ceiling=configured_ceiling,
            clamped_limit=derived_limit,
            provisioner_headroom=mintable,
            friction_id=friction_id,
        )
    _last_i1_disposition = disposition


def reset_capacity_derivation_state() -> None:
    """Clear edge-trigger state (tests only)."""
    global _last_i1_disposition, _last_limit_derived
    _last_i1_disposition = None
    _last_limit_derived = None


def _write_capacity_fields(
    *,
    standard: dict[str, int],
    operator: dict[str, int],
    live_by_lane: dict[str, int],
) -> dict[str, int | str | dict[str, dict[str, int]]]:
    from services.git_integration_worker.cursor_sdk_lane_regime import (
        lane_b_regime_active,
    )
    from services.git_integration_worker.cursor_sdk_worktree_registry import (
        isolated_write_ceiling,
        mintable_worktrees,
    )

    std_lim = int(standard["limit"])
    op_lim = int(operator["limit"])
    live_writers = int(live_by_lane.get("A", 0)) + int(live_by_lane.get("B", 0))

    if lane_b_regime_active():
        configured_ceiling = isolated_write_ceiling()
        mintable = mintable_worktrees()
        derived_limit = std_lim
        configured_headroom = configured_ceiling
        lane_a_slots = write_lease_slots("A", gate_limit=configured_headroom)
        lane_b_slots = write_lease_slots("B")
        disposition = evaluate_i1(configured_ceiling, 0, mintable)
        _maybe_emit_regime_on_derivation_events(
            configured_ceiling=configured_ceiling,
            mintable=mintable,
            derived_limit=derived_limit,
            disposition=disposition,
        )
        headroom = lane_b_slots
        write_capacity = derived_limit
    else:
        configured_headroom = std_lim + op_lim
        lane_a_slots = write_lease_slots("A", gate_limit=configured_headroom)
        lane_b_slots = write_lease_slots("B", gate_limit=configured_headroom)
        if default_write_path_is_lane_a():
            headroom = lane_a_slots
            write_capacity = min(configured_headroom, headroom)
        else:
            headroom = lane_b_slots
            write_capacity = configured_headroom
        disposition = evaluate_i1(std_lim, op_lim, headroom)

    write_capacity_detail: dict[str, dict[str, int]] = {
        "lane_a": {"slots": lane_a_slots},
        "lane_b": {"slots": lane_b_slots},
    }
    return {
        "write_capacity": write_capacity,
        "configured_headroom": configured_headroom,
        "live_writers": live_writers,
        "capacity_disposition": disposition,
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
