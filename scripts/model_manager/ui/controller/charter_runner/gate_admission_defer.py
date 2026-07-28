"""Release-before-admit preflight for charter-origin write windows (25956 / R1 L2).

Preflight reads GIW ``/api/v1/git/active-work`` (same surface as
``giw_live_hold``). The durable fence is ``refuse_if_lease_held`` on the
Stargate body — evaluated transactionally in ``CursorDispatchLedger.admit``.

R1 orphan-reclaim soundness: a holder with no live backing is not a wait — G3
escalates on first observation (``queue_stall_lease_keys`` class).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from universal_logging import get_logger

from .giw_live_hold import fetch_giw_active_work_payload

logger = get_logger(__name__)

# Wall bound: long enough for typical cursor-auto implement windows; short
# enough that an overnight gate wedge surfaces before the next session.
DEFER_MAX_AGE_S = float(os.environ.get("CHARTER_GATE_DEFER_MAX_AGE_S", str(45 * 60)))

GatePreflightOutcome = Literal["proceed", "defer", "escalate"]


@dataclass(frozen=True, slots=True)
class GatePreflightResult:
    outcome: GatePreflightOutcome
    holder_dispatch_id: str | None = None
    holder_age_s: float | None = None
    defer_count: int = 0
    escalation_reason: str | None = None
    queue_depth: int = 0


@dataclass
class _GateDeferState:
    first_deferred_at: float
    defer_count: int = 0


_gate_defer_by_root: dict[str, _GateDeferState] = {}


def admission_mode_requires_write_fence(admission_mode: str) -> bool:
    """True when the mode fires a non-read_only cursor-sdk generate body."""
    return admission_mode in ("generate", "autonomous")


def _holder_age_s(holder_started_at: str | None) -> float | None:
    if not holder_started_at:
        return None
    try:
        started = datetime.fromisoformat(holder_started_at.replace("Z", "+00:00"))
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        return max(0.0, (datetime.now(UTC) - started).total_seconds())
    except ValueError:
        return None


def _holder_is_live(
    holder_dispatch_id: str,
    payload: dict[str, Any],
) -> bool:
    """True when GIW reports a live task/op for the holder (not ledger-only)."""
    for op in payload.get("active_ops") or []:
        if isinstance(op, dict) and str(op.get("op_id") or "") == holder_dispatch_id:
            return True
    cursor = payload.get("cursor_dispatches")
    if isinstance(cursor, dict):
        for raw in cursor.get("dispatch_ids") or []:
            if str(raw) == holder_dispatch_id:
                return True
    return False


def _orphan_holder_no_live_backing(
    holder_dispatch_id: str,
    payload: dict[str, Any],
) -> bool:
    """``queue_stall_lease_keys`` class: holder row without live task backing."""
    lease = payload.get("write_lease")
    if not isinstance(lease, dict):
        return False
    if str(lease.get("holder_dispatch_id") or "") != holder_dispatch_id:
        return False
    return not _holder_is_live(holder_dispatch_id, payload)


def record_gate_defer(root_id: str, *, now: float | None = None) -> int:
    """Bump cumulative defer count; return the new total."""
    now = time.time() if now is None else now
    state = _gate_defer_by_root.get(root_id)
    if state is None:
        _gate_defer_by_root[root_id] = _GateDeferState(first_deferred_at=now, defer_count=1)
        return 1
    state.defer_count += 1
    return state.defer_count


def clear_gate_defer(root_id: str) -> None:
    _gate_defer_by_root.pop(root_id, None)


def gate_defer_count(root_id: str) -> int:
    state = _gate_defer_by_root.get(root_id)
    return state.defer_count if state is not None else 0


async def preflight_write_lease(*, root_id: str) -> GatePreflightResult:
    """Cheap lease preflight before charter fire_window (does not burn window_index)."""
    payload = await fetch_giw_active_work_payload()
    if payload is None:
        return GatePreflightResult(outcome="proceed")

    lease = payload.get("write_lease")
    if not isinstance(lease, dict):
        return GatePreflightResult(outcome="proceed")

    holder_id = str(lease.get("holder_dispatch_id") or "").strip()
    if not holder_id:
        return GatePreflightResult(outcome="proceed")

    holder_age = _holder_age_s(
        lease.get("holder_started_at")
        if isinstance(lease.get("holder_started_at"), str)
        else None
    )
    queue_depth = int(lease.get("queue_depth") or 0)
    defer_state = _gate_defer_by_root.get(root_id)
    defer_count = defer_state.defer_count if defer_state is not None else 0

    if _orphan_holder_no_live_backing(holder_id, payload):
        logger.error(
            "charter gate preflight orphan holder root=%s holder=%s queue_depth=%s",
            root_id,
            holder_id,
            queue_depth,
        )
        return GatePreflightResult(
            outcome="escalate",
            holder_dispatch_id=holder_id,
            holder_age_s=holder_age,
            defer_count=defer_count,
            escalation_reason="orphan_holder_no_live_backing",
            queue_depth=queue_depth,
        )

    if defer_state is not None:
        age_since_first = time.time() - defer_state.first_deferred_at
        if age_since_first >= DEFER_MAX_AGE_S:
            logger.error(
                "charter gate defer age exceeded root=%s holder=%s age_s=%.0f bound=%.0f",
                root_id,
                holder_id,
                age_since_first,
                DEFER_MAX_AGE_S,
            )
            return GatePreflightResult(
                outcome="escalate",
                holder_dispatch_id=holder_id,
                holder_age_s=holder_age,
                defer_count=defer_count,
                escalation_reason="defer_age_exceeded",
                queue_depth=queue_depth,
            )

    new_count = record_gate_defer(root_id)
    return GatePreflightResult(
        outcome="defer",
        holder_dispatch_id=holder_id,
        holder_age_s=holder_age,
        defer_count=new_count,
        queue_depth=queue_depth,
    )


__all__ = [
    "DEFER_MAX_AGE_S",
    "GatePreflightResult",
    "admission_mode_requires_write_fence",
    "clear_gate_defer",
    "gate_defer_count",
    "preflight_write_lease",
    "record_gate_defer",
]
