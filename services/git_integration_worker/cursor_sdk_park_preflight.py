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
from services.git_integration_worker.cursor_sdk_park_ledger import (
    PARK_KIND_DISCARD,
)
from services.git_integration_worker.cursor_sdk_supersede import (
    is_dispatch_live,
    is_dispatch_superseded,
)

TERMINAL_RUN_STATUSES = frozenset({"finished", "error", "cancelled", "expired"})
_TERMINAL_ROW_STATUSES = frozenset({"completed", "failed", "cancelled"})
_CURSOR_SDK_DISPATCH_PREFIX = "cursor-sdk:dispatch:"


class ParkRefusal(StrEnum):
    """Park refusals in precedence order (spec D3 #1–#9, +#10–#11)."""

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
    BOUNDARY_HOLDER = "BOUNDARY_HOLDER"


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
    ParkRefusal.BOUNDARY_HOLDER: (409, True),
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
    ambiguous_dispatch_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SubmittedIdResolution:
    """Result of mapping a route ``dispatch_id`` path segment to one ledger row."""

    row: dict[str, Any] | None
    dispatch_id: str | None = None
    ambiguous: bool = False
    candidate_dispatch_ids: tuple[str, ...] = ()


def execution_id_candidates(execution_id: str) -> list[dict[str, Any]]:
    conn = CursorDispatchLedger.instance()._connect()
    cur = conn.execute(
        "SELECT rowid AS _rowid, * FROM cursor_sdk_dispatches WHERE execution_id=?",
        (execution_id,),
    )
    return [{k: row[k] for k in row.keys()} for row in cur.fetchall()]


def _lineage_head(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Park-resume lineage tip: no resume child in-set, else newest ``rowid``."""
    if not candidates:
        return None
    ids = {str(c["dispatch_id"]) for c in candidates}
    heads = [
        c
        for c in candidates
        if str(c.get("park_resumed_by") or "") not in ids
    ]
    if not heads:
        return None
    if len(heads) == 1:
        return heads[0]
    return max(heads, key=lambda c: int(c.get("_rowid") or 0))


def resolve_submitted_park_id(submitted_id: str) -> SubmittedIdResolution:
    """Map generate-admission ``execution_id`` (or prefixed form) to one ledger row."""
    stripped = submitted_id.removeprefix(_CURSOR_SDK_DISPATCH_PREFIX)
    row = load_park_candidate_row(stripped)
    if row is not None:
        return SubmittedIdResolution(row=row, dispatch_id=str(row["dispatch_id"]))
    candidates = execution_id_candidates(stripped)
    if not candidates:
        return SubmittedIdResolution(row=None)
    if len(candidates) == 1:
        c = candidates[0]
        return SubmittedIdResolution(row=c, dispatch_id=str(c["dispatch_id"]))
    non_terminal = [
        c
        for c in candidates
        if str(c.get("status") or "") not in _TERMINAL_ROW_STATUSES
    ]
    live = [
        c
        for c in non_terminal
        if is_dispatch_live(dispatch_id=str(c["dispatch_id"]))
    ]
    if len(live) == 1:
        c = live[0]
        return SubmittedIdResolution(row=c, dispatch_id=str(c["dispatch_id"]))
    if len(non_terminal) == 1:
        c = non_terminal[0]
        return SubmittedIdResolution(row=c, dispatch_id=str(c["dispatch_id"]))
    if not non_terminal:
        head = _lineage_head(candidates)
        if head is not None:
            return SubmittedIdResolution(
                row=head, dispatch_id=str(head["dispatch_id"])
            )
    if len(non_terminal) >= 2 or len(live) > 1:
        ids = tuple(sorted(str(c["dispatch_id"]) for c in non_terminal or candidates))
        return SubmittedIdResolution(
            row=None, ambiguous=True, candidate_dispatch_ids=ids
        )
    return SubmittedIdResolution(row=None)


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


def preflight_park(
    dispatch_id: str,
    *,
    intent_id: str | None = None,
    mode: str = "cancel",
) -> ParkPreflight:
    """Decide the D3 refusal for *dispatch_id* without touching any state.

    ``BOUNDARY_HOLDER`` is decided in ``park_for_restart_sweep`` (arm holder /
    conductor while a boundary drain is armed), not here.

    *dispatch_id* may be the ledger ``dispatch_id`` or the generate-admit
    ``execution_id`` (bare or ``cursor-sdk:dispatch:``-prefixed).
    """
    discard = mode == "discard"
    resolution = resolve_submitted_park_id(dispatch_id)
    if resolution.ambiguous:
        return ParkPreflight(
            refusal=ParkRefusal.NOT_LIVE_HERE,
            row=None,
            detail=(
                "ambiguous execution_id: "
                f"{', '.join(resolution.candidate_dispatch_ids)}"
            ),
            ambiguous_dispatch_ids=resolution.candidate_dispatch_ids,
        )
    if resolution.row is None or resolution.dispatch_id is None:
        return ParkPreflight(refusal=ParkRefusal.NOT_FOUND, row=None)
    dispatch_id = resolution.dispatch_id
    row = (
        resolution.row
        if "_nest_parent_status" in resolution.row
        else load_park_candidate_row(dispatch_id)
    )
    if row is None:
        return ParkPreflight(refusal=ParkRefusal.NOT_FOUND, row=None)
    status = str(row.get("status") or "")
    if status in _TERMINAL_ROW_STATUSES:
        park_kind = row.get("park_kind")
        if discard:
            if park_kind == PARK_KIND_DISCARD:
                return ParkPreflight(
                    refusal=ParkRefusal.ALREADY_TERMINAL,
                    row=row,
                    already_parked=True,
                    detail=f"row status={status!r} park_kind={park_kind!r}",
                )
            if park_kind and row.get("park_resumed_by") is None:
                return ParkPreflight(refusal=None, row=row)
        parked = bool(park_kind)
        same_intent = intent_id is None or row.get("park_intent_id") == intent_id
        return ParkPreflight(
            refusal=ParkRefusal.ALREADY_TERMINAL,
            row=row,
            already_parked=parked and same_intent,
            detail=f"row status={status!r}",
        )
    if not is_dispatch_live(dispatch_id=dispatch_id):
        if discard:
            return ParkPreflight(
                refusal=None,
                row=row,
                detail="idle row — discard proceeds without bridge cancel",
            )
        return ParkPreflight(
            refusal=ParkRefusal.NOT_LIVE_HERE,
            row=row,
            detail="no live bridge run registered in this process",
        )
    if is_dispatch_superseded(dispatch_id=dispatch_id):
        return ParkPreflight(refusal=ParkRefusal.SUPERSEDE_IN_FLIGHT, row=row)
    if not discard:
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
        from services.git_integration_worker.cursor_sdk_store_locus import (
            resolve_sdk_store_dir,
        )

        if (
            resolve_sdk_store_dir(
                parent_id=dispatch_id, state_root=row.get("state_root")
            )
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
    "SubmittedIdResolution",
    "execution_id_candidates",
    "load_park_candidate_row",
    "preflight_park",
    "resolve_submitted_park_id",
]
