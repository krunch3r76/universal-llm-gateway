"""Conductor wait / exit-and-persist closeout reasons (Mission E §5)."""

from __future__ import annotations

CONDUCTOR_ROW_PINNED = "conductor_row_pinned"
CONDUCTOR_EXIT_PERSIST = "conductor_exit_persist"
CONDUCTOR_NEST_IN_FLIGHT = "conductor_nest_in_flight"

_LIVE_NEST = frozenset({"queued", "admitted", "running", "parked_waiting"})


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
