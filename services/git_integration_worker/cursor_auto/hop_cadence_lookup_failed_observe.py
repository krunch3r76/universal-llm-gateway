"""LOOKUP_FAILED observation for hop-cadence predecessor capture (arc 7186).

Callers are ``capture_predecessor_at_hop`` on the empty-incumbent path.
The signal exists so a later join-half repair can see *which predicate*
rejected *which snap rows*, not merely that ``incumbents_on_lane`` was empty.

Invariant: classify+emit is strictly non-throwing. A raise here must not
replace ``PredecessorConfirmError`` with a crash (b280b46 family).
"""

from __future__ import annotations

from typing import Any

from universal_event_bus import Event, event_factory
from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_events import emit_frontier_event

logger = get_logger(__name__)

LOOKUP_FAILED_ROW_CAP = 16
_STATUS_OK = frozenset({"pending", "running"})
_PURPOSE_OK = frozenset({"operator-proxy", "mission", "operator_proxy"})

# First-reject tokens match incumbents_on_lane predicate order (turn 161).
REJECT_NOT_A_ROW = "not_a_row"
REJECT_STATUS = "status_miss"
REJECT_PURPOSE = "purpose_miss"
REJECT_PARENT_THREAD = "parent_thread_miss"
REJECT_EXECUTION_ID = "missing_execution_id"
REJECT_ACCEPTED = "accepted"

SNAP_KIND_EMPTY = "empty"
SNAP_KIND_FAIL_OPEN = "fail_open"
SNAP_KIND_ROWS_PRESENT = "rows_present"


def first_incumbent_reject(row: Any, lane: str) -> str:
    """Return the first ``incumbents_on_lane`` predicate that rejects ``row``.

    Order is load-bearing: status, then purpose, then parent_thread, then
    nonempty execution_id — the same short-circuit ``incumbents_on_lane`` uses.
    """
    if not isinstance(row, dict):
        return REJECT_NOT_A_ROW
    status = str(row.get("status") or "")
    if status not in _STATUS_OK:
        return REJECT_STATUS
    purpose = str(row.get("purpose") or "").strip().lower()
    if purpose not in _PURPOSE_OK:
        return REJECT_PURPOSE
    row_lane = str(row.get("parent_thread") or "").strip()
    if row_lane != lane:
        return REJECT_PARENT_THREAD
    exec_id = str(row.get("execution_id") or "").strip()
    if not exec_id:
        return REJECT_EXECUTION_ID
    return REJECT_ACCEPTED


def _row_detail(row: Any, lane: str) -> dict[str, Any]:
    pred = first_incumbent_reject(row, lane)
    if not isinstance(row, dict):
        return {
            "parent_thread": "",
            "purpose": "",
            "status": "",
            "execution_id_nonempty": False,
            "first_reject": pred,
        }
    return {
        "parent_thread": str(row.get("parent_thread") or ""),
        "purpose": str(row.get("purpose") or ""),
        "status": str(row.get("status") or ""),
        "execution_id_nonempty": bool(str(row.get("execution_id") or "").strip()),
        "first_reject": pred,
    }


def classify_lookup_failed_snap(
    snap: dict[str, Any] | None,
    *,
    thread_id: str,
    registration_id: str,
    watch_reg_hit: bool,
    row_cap: int = LOOKUP_FAILED_ROW_CAP,
) -> dict[str, Any]:
    """Build the LOOKUP_FAILED observe payload from the fire snap.

    ``observed_at`` is copied from the snap (stamped at GET read), not minted
    here. ``snap_kind`` distinguishes empty store from fail-open from
    rows-present-but-all-filtered — those are three diagnoses, not one.
    """
    snap_dict = snap if isinstance(snap, dict) else {}
    fail_open = bool(snap_dict.get("fail_open"))
    rows = snap_dict.get("rows") if isinstance(snap_dict.get("rows"), list) else []
    lane = (thread_id or "").strip()
    details: list[dict[str, Any]] = []
    for row in rows:
        if len(details) >= row_cap:
            break
        details.append(_row_detail(row, lane))
    if fail_open:
        snap_kind = SNAP_KIND_FAIL_OPEN
    elif not rows:
        snap_kind = SNAP_KIND_EMPTY
    else:
        snap_kind = SNAP_KIND_ROWS_PRESENT
    running_raw = snap_dict.get("running_count")
    free_raw = snap_dict.get("free_slots")
    return {
        "thread_id": thread_id,
        "registration_id": registration_id,
        "observed_at": snap_dict.get("observed_at"),
        "snap_kind": snap_kind,
        "snap_empty": not rows,
        "fail_open": fail_open,
        "total_rows": len(rows),
        "running_count": int(running_raw) if running_raw is not None else None,
        "free_slots": int(free_raw) if free_raw is not None else None,
        "watch_reg_hit": bool(watch_reg_hit),
        "row_details": details,
        "row_detail_cap": row_cap,
        "row_detail_omitted": max(0, len(rows) - row_cap),
        "lane_empty": not lane,
    }


@event_factory
def GiwCursorAutoHopCadenceLookupFailedObserve(  # noqa: N802
    thread_id: str,
    registration_id: str,
    observed_at: str | None,
    snap_kind: str,
    snap_empty: bool,
    fail_open: bool,
    total_rows: int,
    running_count: int | None,
    free_slots: int | None,
    watch_reg_hit: bool,
    row_details: list[dict[str, Any]],
    row_detail_cap: int,
    row_detail_omitted: int,
    lane_empty: bool,
) -> Event:
    """Per-row first-reject snapshot when predecessor lookup finds no incumbent."""
    return Event(
        signal="giw.cursor_auto.hop_cadence_lookup_failed_observe",
        payload={
            "thread_id": thread_id,
            "registration_id": registration_id,
            "observed_at": observed_at,
            "snap_kind": snap_kind,
            "snap_empty": snap_empty,
            "fail_open": fail_open,
            "total_rows": total_rows,
            "running_count": running_count,
            "free_slots": free_slots,
            "watch_reg_hit": watch_reg_hit,
            "row_details": row_details,
            "row_detail_cap": row_detail_cap,
            "row_detail_omitted": row_detail_omitted,
            "lane_empty": lane_empty,
        },
        scope="node",
        role="observation",
    )


def observe_lookup_failed_nonthrowing(
    *,
    thread_id: str,
    registration_id: str,
    snap: dict[str, Any] | None,
    watch_reg_hit: bool,
) -> None:
    """Emit LOOKUP_FAILED observe; swallow every failure so capture is unchanged."""
    try:
        payload = classify_lookup_failed_snap(
            snap,
            thread_id=thread_id,
            registration_id=registration_id,
            watch_reg_hit=watch_reg_hit,
        )
        emit_frontier_event(GiwCursorAutoHopCadenceLookupFailedObserve(**payload))
    except Exception as exc:  # noqa: BLE001 — observation must not own the fire path
        logger.warning(
            "hop_cadence lookup_failed observe failed thread=%s reg=%s: %s",
            thread_id,
            registration_id,
            exc,
        )


__all__ = [
    "LOOKUP_FAILED_ROW_CAP",
    "REJECT_ACCEPTED",
    "REJECT_EXECUTION_ID",
    "REJECT_NOT_A_ROW",
    "REJECT_PARENT_THREAD",
    "REJECT_PURPOSE",
    "REJECT_STATUS",
    "SNAP_KIND_EMPTY",
    "SNAP_KIND_FAIL_OPEN",
    "SNAP_KIND_ROWS_PRESENT",
    "classify_lookup_failed_snap",
    "first_incumbent_reject",
    "observe_lookup_failed_nonthrowing",
]
