"""Conductor wait / exit-and-persist closeout reasons (Mission E §5)."""

from __future__ import annotations

import json
from typing import Any

CONDUCTOR_ROW_PINNED = "conductor_row_pinned"
CONDUCTOR_ROW_HOP = "conductor_row_hop"
CONDUCTOR_EXIT_PERSIST = "conductor_exit_persist"
CONDUCTOR_NEST_IN_FLIGHT = "conductor_nest_in_flight"

_LIVE_NEST = frozenset({"queued", "admitted", "running", "parked_waiting"})
_HOST_RUNNING = frozenset({"pending", "running"})
SKIP_GATE_LIVE_EXTERNAL = "live_external_gate"
SKIP_GATE_PROBE_INDETERMINATE = "probe_indeterminate"


def _record_data_from_row(row: dict[str, Any]) -> dict[str, Any]:
    record_json = str(row.get("record_json") or "")
    try:
        data = json.loads(record_json) if record_json else {}
    except json.JSONDecodeError:
        data = {}
    return data if isinstance(data, dict) else {}


def mission_lane_from_conductor_row(row: dict[str, Any]) -> str:
    """Bus private-request lane used to join CDP external gate executions."""
    rec = _record_data_from_row(row)
    summoning = str(rec.get("summoning_thread_id") or "").strip()
    if summoning:
        return summoning
    return str(row.get("thread_id") or "").strip()


_GATE_OCCUPANCY_PURPOSES = frozenset(
    {"review", "operator-proxy", "operator_proxy", "mission"}
)


def live_external_gate_for_lane(
    snap: dict[str, Any],
    mission_lane: str,
    *,
    exclude_execution_id: str | None = None,
) -> bool:
    """True when active-work has a pending/running gate row bound to ``mission_lane``."""
    from claude_bundles.hop_cadence_id_map import ids_match_exclude, normalize_exclude_ids
    from claude_bundles.hop_cadence_seat_snap import identity_rows

    lane = (mission_lane or "").strip()
    if not lane or not snap:
        return False
    exclude = normalize_exclude_ids(exclude_execution_id)
    for aw_row in identity_rows(snap):
        status = str(aw_row.get("status") or "")
        if status not in _HOST_RUNNING:
            continue
        purpose = str(aw_row.get("purpose") or "").strip().lower()
        if purpose not in _GATE_OCCUPANCY_PURPOSES:
            continue
        exec_id = str(aw_row.get("execution_id") or "").strip()
        if exec_id and ids_match_exclude(exec_id, exclude):
            continue
        parent = str(aw_row.get("parent_thread") or "").strip()
        if parent == lane:
            return True
    return False


def read_external_gate_lane_snapshot() -> dict[str, Any]:
    """Shared CDP lane snap for conductor external-gate occupancy (P1.2)."""
    from services.git_integration_worker.cursor_auto.cdp_escalation import (
        read_cdp_lane_snapshot,
    )

    return read_cdp_lane_snapshot()


def external_gate_hop_verdict(row: dict[str, Any]) -> tuple[str, str | None]:
    """Occupancy probe for ``hop_owed`` (P1.2 / P1.4).

    Returns ``(verdict, skip_gate)`` where *skip_gate* is set when the reactor
    must not POST a successor. Probe fails open when no gate is owed
    (``closeout_harvest_owed`` false); fails closed when harvest is owed and
    the snap is empty/indeterminate.
    """
    rec = _record_data_from_row(row)
    harvest_owed = rec.get("closeout_harvest_owed") is True
    lane = mission_lane_from_conductor_row(row)
    try:
        snap = read_external_gate_lane_snapshot()
    except Exception:
        snap = {}
    if not snap:
        if harvest_owed:
            return "indeterminate_closed", SKIP_GATE_PROBE_INDETERMINATE
        return "indeterminate_open", None
    if live_external_gate_for_lane(snap, lane):
        return "live", SKIP_GATE_LIVE_EXTERNAL
    return "clear", None


def _packet_is_conductor(
    packet_kind: str | None, packet_text: str | None
) -> bool:
    if packet_kind == "conductor":
        return True
    if packet_text:
        from services.git_integration_worker.cursor_sdk_packet import (
            extract_packet_kind_from_packet,
        )

        return extract_packet_kind_from_packet(packet_text) == "conductor"
    return False


def conductor_row_hop_degraded_reason(
    *,
    body: str,
    packet_text: str | None = None,
    packet_kind: str | None = None,
) -> str | None:
    """ROW_HOP grades as a designed exit-and-continue, not gate_d/work."""
    if not _packet_is_conductor(packet_kind, packet_text):
        return None
    from claude_bundles.conductor_stop import parse_stop_tokens

    if "ROW_HOP" in parse_stop_tokens(body).tokens:
        return CONDUCTOR_ROW_HOP
    return None


def conductor_row_pinned_degraded_reason(
    *,
    body: str,
    packet_text: str | None = None,
    packet_kind: str | None = None,
) -> str | None:
    """ROW_PINNED / other exit-persist tokens grade consult, not gate_d/work."""
    if not _packet_is_conductor(packet_kind, packet_text):
        return None
    from claude_bundles.conductor_stop import (
        EXIT_PERSIST_STOPS,
        parse_stop_tokens,
    )

    tokens = parse_stop_tokens(body).tokens
    if "ROW_PINNED" in tokens:
        return CONDUCTOR_ROW_PINNED
    if tokens & EXIT_PERSIST_STOPS:
        return CONDUCTOR_EXIT_PERSIST
    return None


def conductor_has_live_nested(*, dispatch_id: str | None) -> bool:
    """True when a nest_under child is still queued/admitted/running/parked."""
    if not dispatch_id:
        return False
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    ledger = CursorDispatchLedger.instance()
    children = ledger.list_nested_children(parent_dispatch_id=dispatch_id)
    for child_id in children:
        row = ledger.dispatch_status_by_id(dispatch_id=child_id)
        if row and row.get("status") in _LIVE_NEST:
            return True
    return False
