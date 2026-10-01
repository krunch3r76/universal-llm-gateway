"""Keep a conductor mission's lane branch alive across its own closeouts.

A conductor mission runs as a chain of short dispatches on one worker thread
and one Lane-B branch: each hop ends with a designed stop (``ROW_HOP``,
``CONSULT_PENDING``, ``PARKED_TRANSPORT``, ``ROW_PINNED``, …) and the
substrate admits the successor on the same branch. Lane settlement at closeout
did not know that. A hop that declared ``land_disposition: unlanded <tip>`` had
its branch archived and deleted on the spot (``cursor_sdk_branch_discharge``);
one that declared nothing opened a branch debt; a nested child that ended
failed or cancelled marked the shared branch abandoned. Worker 13713 lost
``cursor-sdk/lane-13713`` twice inside one mission (archive tags
``lane-13713-af2602e4`` and ``lane-13713-1b0635e3``, 2026-10-01) and worker
13691 hit ``CURSOR_LANE_PIN_FAILED`` on resume (a:37043).

This module answers one question for the settlement paths: is this lane still
owned by an open mission? Callers are
``cursor_sdk_branch_terminal.settle_lane_branch`` (skips discharge and debt)
and ``delivery_assembly.lane_settlement`` (skips the abandoned mark). Only a
conductor closeout that carries ``DONE``, or a closeout with no open conductor
above it, settles the branch. Reads the dispatch ledger; writes nothing.
"""

from __future__ import annotations

import json
from typing import Any

from claude_bundles.conductor_stop import parse_designed_stop_tokens
from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_conductor_identity import (
    is_conductor_dispatch_row,
)

logger = get_logger(__name__)

RETAINED_FOR_MISSION = "retained_for_mission"
_LIVE_STATUSES = frozenset({"queued", "admitted", "running", "parked_waiting"})
_MAX_NEST_WALK = 12


def _load_row(dispatch_id: str) -> dict[str, Any] | None:
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def _record(row: dict[str, Any]) -> dict[str, Any]:
    raw = str(row.get("record_json") or "")
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def closeout_stop_tokens(
    row: dict[str, Any], closeout_text: str | None
) -> frozenset[str]:
    """Designed stop tokens for a row: the closeout text first, then the ledger stamp.

    Settlement can run before ``merge_conductor_closeout_hop_authority`` has
    stamped ``closeout_stop_tokens``, so the text the conductor wrote is read
    directly; the stamp is the fallback for callers that hold no text (a
    parked parent reached through ``nest_under``).
    """
    if closeout_text:
        parsed = parse_designed_stop_tokens(closeout_text)
        tokens = parsed.designed_tokens or parsed.tokens
        if tokens:
            return frozenset(str(t).upper() for t in tokens)
    raw = _record(row).get("closeout_stop_tokens")
    if isinstance(raw, list):
        return frozenset(str(t).upper() for t in raw)
    return frozenset()


def conductor_mission_open(
    row: dict[str, Any], *, closeout_text: str | None
) -> str | None:
    """Reason the mission this conductor row belongs to is still open, else None.

    Open means the row is live, or terminal without ``DONE``: every other
    designed stop continues on the same branch, and a crash (no token at all)
    is resumed on it. A non-conductor row never opens a mission by itself.
    """
    if not is_conductor_dispatch_row(row):
        return None
    status = str(row.get("status") or "")
    if status in _LIVE_STATUSES:
        return f"conductor_live:{status}"
    tokens = closeout_stop_tokens(row, closeout_text)
    if "DONE" in tokens:
        return None
    label = ",".join(sorted(tokens)) if tokens else "crash"
    return f"conductor_mission_open:{label}"


def lane_retention_reason(
    *,
    dispatch_id: str,
    thread_id: str | None = None,
    closeout_text: str | None = None,
) -> str | None:
    """Why the lane branch must survive this closeout, or None when it may settle.

    Walks ``nest_under`` from the closing row toward its root: a nested child
    on a shared lane retains the branch when any ancestor is a conductor whose
    mission is open. ``thread_id`` is accepted for symmetry with the settlement
    call sites; the decision rests on ledger rows. Never raises — a ledger that
    cannot be read settles as before (returns None) and logs a warning.
    """
    _ = thread_id
    try:
        row = _load_row(dispatch_id)
    except Exception as exc:  # noqa: BLE001 — settlement must not fail on this
        logger.warning(
            "lane retention: ledger read failed dispatch=%s err=%s", dispatch_id, exc
        )
        return None
    if row is None:
        return None
    reason = conductor_mission_open(row, closeout_text=closeout_text)
    if reason:
        return reason
    seen: set[str] = {dispatch_id}
    parent_id = str(_record(row).get("nest_under") or "").strip()
    hops = 0
    while parent_id and parent_id not in seen and hops < _MAX_NEST_WALK:
        seen.add(parent_id)
        hops += 1
        try:
            parent = _load_row(parent_id)
        except Exception as exc:  # noqa: BLE001 — same posture as above
            logger.warning(
                "lane retention: ledger read failed dispatch=%s err=%s", parent_id, exc
            )
            return None
        if parent is None:
            break
        parent_reason = conductor_mission_open(parent, closeout_text=None)
        if parent_reason:
            return f"nested_under_open_conductor:{parent_id}:{parent_reason}"
        parent_id = str(_record(parent).get("nest_under") or "").strip()
    return None


__all__ = [
    "RETAINED_FOR_MISSION",
    "closeout_stop_tokens",
    "conductor_mission_open",
    "lane_retention_reason",
]
