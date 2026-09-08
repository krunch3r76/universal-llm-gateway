"""Fail-closed park preflight — the D3 refusal ladder for ``park_for_restart``.

Every refusal is decided *before* the bridge ``CancelRun`` and mutates nothing:
no ledger status change, no git, no registry. Precedence is the spec order
(#1 ``NOT_FOUND`` … #9 ``CANCEL_FAILED``) plus one implement-time amendment,
``RUN_ALREADY_TERMINAL``: the bridge run finished but the worker thread has
not unwound yet — cancel is inapplicable and aborting the bridge would only
destroy the closeout in flight, so the sweep treats it like ``NOT_LIVE_HERE``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)
from services.git_integration_worker.cursor_sdk_supersede import (
    is_dispatch_live,
    is_dispatch_superseded,
)

TERMINAL_RUN_STATUSES = frozenset({"finished", "error", "cancelled", "expired"})
_TERMINAL_ROW_STATUSES = frozenset({"completed", "failed", "cancelled"})


class ParkRefusal(StrEnum):
    """Park refusals in precedence order (spec D3 #1–#9, +#10 amendment)."""

    NOT_FOUND = "NOT_FOUND"
    ALREADY_TERMINAL = "ALREADY_TERMINAL"
    NOT_LIVE_HERE = "NOT_LIVE_HERE"
    SUPERSEDE_IN_FLIGHT = "SUPERSEDE_IN_FLIGHT"
    NEST_CHAIN = "NEST_CHAIN"
    NOT_RESUMABLE_YET = "NOT_RESUMABLE_YET"
    STATE_ROOT_MISSING = "STATE_ROOT_MISSING"
    LANE_B_UNPINNED = "LANE_B_UNPINNED"
    CANCEL_FAILED = "CANCEL_FAILED"
    RUN_ALREADY_TERMINAL = "RUN_ALREADY_TERMINAL"


# refusal → (http_status, retryable)
REFUSAL_HTTP: dict[ParkRefusal, tuple[int, bool]] = {
    ParkRefusal.NOT_FOUND: (404, False),
    ParkRefusal.ALREADY_TERMINAL: (409, False),
    ParkRefusal.NOT_LIVE_HERE: (409, False),
    ParkRefusal.SUPERSEDE_IN_FLIGHT: (409, False),
    ParkRefusal.NEST_CHAIN: (422, False),
    ParkRefusal.NOT_RESUMABLE_YET: (409, True),
    ParkRefusal.STATE_ROOT_MISSING: (422, False),
    ParkRefusal.LANE_B_UNPINNED: (422, False),
    ParkRefusal.CANCEL_FAILED: (503, False),
    ParkRefusal.RUN_ALREADY_TERMINAL: (409, False),
}

# Refusals that leave nothing for a restart to wait on once the row closes.
SELF_CLEARING_REFUSALS = frozenset(
    {ParkRefusal.NOT_LIVE_HERE, ParkRefusal.RUN_ALREADY_TERMINAL}
)
# Refusals a recycle must read as "park cannot free this occupant" → kill rung.
HARD_REFUSALS = frozenset(
    {
        ParkRefusal.CANCEL_FAILED,
        ParkRefusal.NEST_CHAIN,
        ParkRefusal.STATE_ROOT_MISSING,
    }
)


@dataclass(frozen=True, slots=True)
class ParkPreflight:
    refusal: ParkRefusal | None
    row: dict[str, Any] | None
    already_parked: bool = False
    detail: str | None = None


def load_park_candidate_row(dispatch_id: str) -> dict[str, Any] | None:
    """Ledger row plus ``_nest_parent_status`` for the nest-chain check."""
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
        if row is None:
            return None
        data = {k: row[k] for k in row.keys()}
        nest_under = data.get("nest_under")
        if not nest_under:
            from services.git_integration_worker.cursor_sdk_work_key_gate import (
                lineage_carriers_from_record,
            )

            nest_under, _hop = lineage_carriers_from_record(data.get("record_json"))
        data["_nest_parent_status"] = None
        if nest_under:
            parent = conn.execute(
                "SELECT status FROM cursor_sdk_dispatches WHERE dispatch_id=?",
                (nest_under,),
            ).fetchone()
            data["_nest_parent_status"] = parent["status"] if parent else None
    return data


def _row_lane(row: dict[str, Any]) -> str | None:
    try:
        rec = json.loads(row.get("record_json") or "{}")
    except json.JSONDecodeError:
        return None
    lane = rec.get("lane") if isinstance(rec, dict) else None
    return str(lane) if lane else None


def _lane_b_unpinned(row: dict[str, Any]) -> bool:
    """True for a Lane-B row whose tree has no active pin (S1 I1 violated)."""
    if _row_lane(row) != "B":
        return False
    source_repo = row.get("source_repo")
    thread_id = row.get("thread_id")
    if not source_repo or not thread_id:
        return True
    from services.git_integration_worker.cursor_sdk_worktree_registry import active_pin

    return (
        active_pin(source_repo=Path(str(source_repo)), thread_id=str(thread_id)) is None
    )


def preflight_park(dispatch_id: str, *, intent_id: str | None = None) -> ParkPreflight:
    """Decide the D3 refusal for *dispatch_id* without touching any state."""
    row = load_park_candidate_row(dispatch_id)
    if row is None:
        return ParkPreflight(refusal=ParkRefusal.NOT_FOUND, row=None)
    status = str(row.get("status") or "")
    if status in _TERMINAL_ROW_STATUSES:
        parked = bool(row.get("park_kind"))
        same_intent = intent_id is None or row.get("park_intent_id") == intent_id
        return ParkPreflight(
            refusal=ParkRefusal.ALREADY_TERMINAL,
            row=row,
            already_parked=parked and same_intent,
            detail=f"row status={status!r}",
        )
    if not is_dispatch_live(dispatch_id=dispatch_id):
        return ParkPreflight(
            refusal=ParkRefusal.NOT_LIVE_HERE,
            row=row,
            detail="no live bridge run registered in this process",
        )
    if is_dispatch_superseded(dispatch_id=dispatch_id):
        return ParkPreflight(refusal=ParkRefusal.SUPERSEDE_IN_FLIGHT, row=row)
    if status == "parked_waiting" or row.get("park_child_dispatch_id"):
        return ParkPreflight(refusal=ParkRefusal.NEST_CHAIN, row=row, detail=status)
    if row.get("_nest_parent_status") == "parked_waiting":
        return ParkPreflight(
            refusal=ParkRefusal.NEST_CHAIN,
            row=row,
            detail="live child of a parked_waiting parent",
        )
    if not row.get("sdk_agent_id"):
        return ParkPreflight(
            refusal=ParkRefusal.NOT_RESUMABLE_YET,
            row=row,
            detail="sdk_agent_id not yet recorded",
        )
    from services.git_integration_worker.cursor_sdk_resume import resolve_sdk_store_dir

    if (
        resolve_sdk_store_dir(parent_id=dispatch_id, state_root=row.get("state_root"))
        is None
    ):
        return ParkPreflight(refusal=ParkRefusal.STATE_ROOT_MISSING, row=row)
    if _lane_b_unpinned(row):
        return ParkPreflight(refusal=ParkRefusal.LANE_B_UNPINNED, row=row)
    return ParkPreflight(refusal=None, row=row)


__all__ = [
    "HARD_REFUSALS",
    "REFUSAL_HTTP",
    "SELF_CLEARING_REFUSALS",
    "TERMINAL_RUN_STATUSES",
    "ParkPreflight",
    "ParkRefusal",
    "load_park_candidate_row",
    "preflight_park",
]
