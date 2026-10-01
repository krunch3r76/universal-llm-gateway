"""Conductor wait / exit-and-persist closeout reasons (Mission E §5)."""

from __future__ import annotations

import json
import time
from typing import Any

CONDUCTOR_ROW_PINNED = "conductor_row_pinned"
CONDUCTOR_ROW_HOP = "conductor_row_hop"
CONDUCTOR_EXIT_PERSIST = "conductor_exit_persist"
CONDUCTOR_NEST_IN_FLIGHT = "conductor_nest_in_flight"

_LIVE_NEST = frozenset({"queued", "admitted", "running", "parked_waiting"})
_HOST_RUNNING = frozenset({"pending", "running"})
SKIP_GATE_LIVE_EXTERNAL = "live_external_gate"
SKIP_GATE_PROBE_INDETERMINATE = "probe_indeterminate"
HOP_CDP_PROBE_INDETERMINATE_AT_KEY = "hop_cdp_probe_indeterminate_at"


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
    from claude_bundles.hop_cadence_id_map import (
        ids_match_exclude,
        normalize_exclude_ids,
    )
    from claude_bundles.hop_cadence_seat_snap import identity_rows, is_live_stream_state

    lane = (mission_lane or "").strip()
    if not lane or not snap:
        return False
    exclude = normalize_exclude_ids(exclude_execution_id)
    for aw_row in identity_rows(snap):
        stream_state = str(aw_row.get("stream_state") or "")
        if not is_live_stream_state(stream_state):
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
    """Shared CDP lane snap for conductor external-gate occupancy (P1.2).

    Uses the 2s control-plane timeout: this runs from GIW background sweeps
    on the asyncio thread, and a 30s connect to a down cdp-ask wedges /health.
    """
    from cdp_ask.lane_snapshot import read_cdp_lane_snapshot_brief

    return read_cdp_lane_snapshot_brief()


def closeout_harvest_owed_from_row(row: dict[str, Any]) -> bool:
    """True when the closeout body or stamp still owes a CDP harvest."""
    rec = _record_data_from_row(row)
    closeout_body = str(rec.get("closeout_body") or "").strip()
    if closeout_body:
        from bus_watch.park_harvest import harvest_still_owed

        return harvest_still_owed(body=closeout_body)
    return rec.get("closeout_harvest_owed") is True


def cdp_ask_health_red() -> bool:
    """True when a cdp-ask ``/health`` probe reports not ok.

    Config misses and transport faults are not red. An empty lane snapshot
    is already the fail-closed signal for a down process; red is the explicit
    unhealthy observation the watchdog treats like that empty snapshot.
    """
    try:
        from scripts.model_manager.ui.controller.service_config import (
            cdp_ask_url_config,
        )
        from scripts.model_manager.ui.model.cdp_ask_status import (
            observe_cdp_ask_health,
        )
    except Exception:
        return False
    cfg = cdp_ask_url_config()
    if cfg is None:
        return False
    host, port, _base = cfg
    try:
        observation = observe_cdp_ask_health(host, port)
    except Exception:
        return False
    return not observation.ok


def _external_gate_snap() -> dict[str, Any]:
    try:
        snap = read_external_gate_lane_snapshot()
    except Exception:
        return {}
    return snap if isinstance(snap, dict) else {}


def _live_gate_blocks(row: dict[str, Any], snap: dict[str, Any]) -> bool:
    rec = _record_data_from_row(row)
    exclude = rec.get("cdp_execution_id") or row.get("execution_id")
    exclude_id = str(exclude).strip() if exclude else None
    return live_external_gate_for_lane(
        snap,
        mission_lane_from_conductor_row(row),
        exclude_execution_id=exclude_id,
    )


def cdp_probe_indeterminate_withholds(row: dict[str, Any]) -> bool:
    """Harvest is owed and the probe is empty or cdp-ask health is red.

    A live gate stream stays a hard block. G4 and G6 are unchanged; this only
    names the indeterminate wait the watchdog may end.
    """
    if not closeout_harvest_owed_from_row(row):
        return False
    snap = _external_gate_snap()
    if _live_gate_blocks(row, snap):
        return False
    if not snap:
        return True
    return cdp_ask_health_red()


def cdp_probe_indeterminate_watchdog_due(
    row: dict[str, Any],
    *,
    now: float | None = None,
) -> bool:
    """True after the row has been terminal longer than twice reactor grace.

    One-shot: ``hop_cdp_probe_indeterminate_at`` suppresses a second admit.
    The reactor still answers ``probe_indeterminate`` immediately.
    """
    rec = _record_data_from_row(row)
    if isinstance(rec.get(HOP_CDP_PROBE_INDETERMINATE_AT_KEY), (int, float)):
        return False
    if not cdp_probe_indeterminate_withholds(row):
        return False
    from services.git_integration_worker.cursor_sdk_park import _terminal_epoch

    terminal = _terminal_epoch(row)
    if terminal is None:
        return False
    now_ts = time.time() if now is None else now
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_budget import (
        load_hop_budget_config,
    )

    grace = load_hop_budget_config().reactor_grace_s
    return (now_ts - terminal) > (2 * grace)


def external_gate_hop_verdict(row: dict[str, Any]) -> tuple[str, str | None]:
    """Occupancy probe for ``hop_owed`` (P1.2 / P1.4).

    Returns ``(verdict, skip_gate)`` where *skip_gate* is set when the reactor
    must not POST a successor. Probe fails open when no gate is owed
    (``closeout_harvest_owed`` false); fails closed when harvest is owed and
    the snap is empty/indeterminate. The watchdog, not this verdict, ends
    that wait after twice the reactor grace.
    """
    harvest_owed = closeout_harvest_owed_from_row(row)
    lane = mission_lane_from_conductor_row(row)
    try:
        snap = read_external_gate_lane_snapshot()
    except Exception:
        snap = {}
    if not snap:
        if harvest_owed:
            return "indeterminate_closed", SKIP_GATE_PROBE_INDETERMINATE
        return "indeterminate_open", None
    if not harvest_owed:
        return "clear", None
    rec = _record_data_from_row(row)
    exclude = rec.get("cdp_execution_id") or row.get("execution_id")
    if live_external_gate_for_lane(snap, lane, exclude_execution_id=exclude):
        return "live", SKIP_GATE_LIVE_EXTERNAL
    return "clear", None


def _packet_is_conductor(packet_kind: str | None, packet_text: str | None) -> bool:
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
    """True when any nested descendant is still live (transitive nest_under walk)."""
    if not dispatch_id:
        return False
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )
    from services.git_integration_worker.cursor_sdk_nest_depth import (
        MAX_WALK_HOPS,
    )

    ledger = CursorDispatchLedger.instance()
    queue: list[str] = list(
        ledger.list_nested_children(parent_dispatch_id=dispatch_id)
    )
    seen: set[str] = {dispatch_id}
    hops = 0
    while queue:
        if hops >= MAX_WALK_HOPS:
            return True
        child_id = queue.pop(0)
        if child_id in seen:
            continue
        seen.add(child_id)
        hops += 1
        row = ledger.dispatch_status_by_id(dispatch_id=child_id)
        if row and row.get("status") in _LIVE_NEST:
            return True
        queue.extend(ledger.list_nested_children(parent_dispatch_id=child_id))
    return False
