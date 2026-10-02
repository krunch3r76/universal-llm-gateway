"""Density harness watchdog — conductor gate for in-row context steer.

``cursor_sdk_closeout.conductor_hop_watchdog`` owns terminal ROW_HOP reactor
timing; this module ticks **live** conductor rows against the stream-backed
visible-context meter and deposits at most one density steer per dispatch.
"""

from __future__ import annotations

from typing import Any, Mapping

from universal_logging import get_logger

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)
from services.git_integration_worker.cursor_sdk_conductor_identity import (
    is_conductor_dispatch_row,
)
from services.git_integration_worker.cursor_sdk_row_density_harness_meter import (
    maybe_deposit_density_steer,
    maybe_park_ignored_density_steer,
    sync_density_steer_delivery,
)

logger = get_logger(__name__)

_LIVE_STATUSES = ("queued", "admitted", "running", "parked_waiting")


def running_dispatch_row(dispatch_id: str) -> dict[str, Any] | None:
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    if row is None:
        return None
    mapped = {k: row[k] for k in row.keys()}
    status = str(mapped.get("status") or "")
    if status not in _LIVE_STATUSES:
        return None
    return mapped


def maybe_tick_density_harness_steer(
    *,
    dispatch_id: str,
    tool_call_count: int | None = None,
    submitted_id: str | None = None,
) -> dict[str, Any]:
    """Conductor gate + one-shot steer deposit; optional ignored-steer park."""
    row = running_dispatch_row(dispatch_id)
    if row is None:
        return {"steered": False, "reason": "not_live"}
    if not is_conductor_dispatch_row(row):
        return {"steered": False, "reason": "not_conductor"}
    if tool_call_count is not None:
        sync_density_steer_delivery(row, tool_call_count=tool_call_count)
        row = running_dispatch_row(dispatch_id) or row
        park = maybe_park_ignored_density_steer(row, tool_call_count=tool_call_count)
        if park is not None:
            return {"steered": False, "parked": True, "park": park.refusal}
    sid = submitted_id or f"density-harness:{dispatch_id}"
    deposit = maybe_deposit_density_steer(row, submitted_id=sid)
    if deposit is None:
        return {"steered": False, "reason": "threshold_or_latched"}
    return {
        "steered": True,
        "entry_id": deposit.entry_id,
        "authority_turn_id": deposit.authority_turn_id,
    }


def sweep_density_harness_steers(ledger: CursorDispatchLedger | None = None) -> int:
    """Tick every live conductor row once (sweeper cadence). Returns steer count."""
    led = ledger or CursorDispatchLedger.instance()
    steered = 0
    with led._connect() as conn:
        rows = conn.execute(
            "SELECT dispatch_id FROM cursor_sdk_dispatches "
            "WHERE status IN ('admitted','running') AND contract='conductor'"
        ).fetchall()
    for row in rows:
        dispatch_id = str(row["dispatch_id"])
        outcome = maybe_tick_density_harness_steer(dispatch_id=dispatch_id)
        if outcome.get("steered"):
            steered += 1
    return steered


__all__ = [
    "maybe_tick_density_harness_steer",
    "running_dispatch_row",
    "sweep_density_harness_steers",
]
