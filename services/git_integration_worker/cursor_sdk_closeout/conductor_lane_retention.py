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
``cursor_sdk_branch_terminal.settle_lane_branch`` (skips discharge and debt),
``delivery_assembly.lane_settlement`` (``lane_retention_lookup``; skips the
abandoned mark, including when the ledger read failed), and
``routes.cursor_sdk._mark_lane_b_abandon_disposition`` (the failed-terminal
mark that ``gc_merged_dispatch_branches`` deletes on). A retain also stamps
``lane_retained_for_mission`` on the owning row and a
``retained_for_mission`` disposition the reap does not delete. Only a conductor
closeout that carries ``DONE``, or a closeout with no open conductor above it
on this branch, settles the branch. A nested limb's own lane branch is not
the ancestor's and settles. Reads the dispatch ledger; the marker write is
the one mutation.
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
_RETAINED_MARKER_KEY = "lane_retained_for_mission"
_LIVE_STATUSES = frozenset({"queued", "admitted", "running", "parked_waiting"})
_MAX_NEST_WALK = 12


class _LedgerReadError(Exception):
    """A ledger read inside the retention walk failed.

    Not a retention reason. ``lane_retention_lookup`` surfaces it as
    ``lookup_ok=False``. ``lane_retention_reason`` collapses it to None so
    route abandon and branch discharge keep their existing None contract.
    """


def _load_latest_terminal_conductor_on_thread(
    thread_id: str,
) -> dict[str, Any] | None:
    """Latest terminal conductor row on one worker thread (hop_seq, then terminal time)."""
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )
    from services.git_integration_worker.cursor_sdk_ledger_hop import (
        hop_fields_from_record_json,
    )
    from services.git_integration_worker.cursor_sdk_park import _terminal_epoch

    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches "
            "WHERE thread_id=? AND status IN ('completed','failed','cancelled')",
            (thread_id,),
        ).fetchall()
    best: dict[str, Any] | None = None
    best_seq = -1
    best_ts = -1.0
    for row in rows:
        mapped = {k: row[k] for k in row.keys()}
        if not is_conductor_dispatch_row(mapped):
            continue
        hop_fields = hop_fields_from_record_json(str(mapped.get("record_json") or ""))
        hop_seq = hop_fields.get("hop_seq")
        seq_i = int(hop_seq) if isinstance(hop_seq, int) else 0
        ts = _terminal_epoch(mapped) or 0.0
        if seq_i > best_seq or (seq_i == best_seq and ts >= best_ts):
            best = mapped
            best_seq = seq_i
            best_ts = ts
    return best


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
    row: dict[str, Any],
    *,
    closeout_text: str | None,
    closing: bool = False,
) -> str | None:
    """Reason the mission this conductor row belongs to is still open, else None.

    The closing row is still ``running`` when settlement runs
    (``prepare_closeout_delivery_async`` precedes ``mark_terminal``). Its live
    status is not evidence the mission continues: ``DONE`` settles, any other
    designed stop retains with that token, and no token is a crash resume.
    Ancestors reached through ``nest_under`` keep the live-status shortcut;
    their closeout text is not in hand. A non-conductor row never opens a
    mission by itself.
    """
    if not is_conductor_dispatch_row(row):
        return None
    status = str(row.get("status") or "")
    if not closing and status in _LIVE_STATUSES:
        return f"conductor_live:{status}"
    tokens = closeout_stop_tokens(row, closeout_text)
    if "DONE" in tokens:
        return None
    if not tokens:
        marker = _record(row).get(_RETAINED_MARKER_KEY)
        if isinstance(marker, str) and marker:
            return marker
    label = ",".join(sorted(tokens)) if tokens else "crash"
    return f"conductor_mission_open:{label}"


def _branch_is_row_lane(row: dict[str, Any], branch_name: str | None) -> bool:
    """True when *branch_name* is this row's ``cursor-sdk/lane-{thread_id}``."""
    if not branch_name:
        return False
    thread_id = str(row.get("thread_id") or "").strip()
    if not thread_id:
        return False
    from services.git_integration_worker.cursor_sdk_worktree import lane_branch_name

    return lane_branch_name(thread_id) == branch_name


def _persist_retained_marker(*, dispatch_id: str, branch_name: str, reason: str) -> None:
    """Stamp the retention where a later reap can see it without the closeout text."""
    try:
        from services.git_integration_worker.cursor_dispatch_ledger import (
            CursorDispatchLedger,
        )

        CursorDispatchLedger.instance().merge_record_json(
            dispatch_id=dispatch_id,
            patch={_RETAINED_MARKER_KEY: reason},
        )
    except Exception as exc:  # noqa: BLE001 — the decision already stands
        logger.warning(
            "lane retention marker write failed dispatch=%s err=%s", dispatch_id, exc
        )
    try:
        from services.git_integration_worker.cursor_sdk_lane_b_disposition import (
            mark_lane_b_disposition,
        )

        mark_lane_b_disposition(
            branch_name=branch_name,
            reason=RETAINED_FOR_MISSION,
            dispatch_id=dispatch_id,
        )
    except Exception as exc:  # noqa: BLE001 — record stamp is the other copy
        logger.warning(
            "lane retention disposition write failed branch=%s err=%s",
            branch_name,
            exc,
        )


def _clear_retained_marker(*, dispatch_id: str, branch_name: str) -> None:
    """DONE on this lane releases the marker the reap was honoring."""
    try:
        from services.git_integration_worker.cursor_dispatch_ledger import (
            CursorDispatchLedger,
        )

        CursorDispatchLedger.instance().merge_record_json(
            dispatch_id=dispatch_id,
            patch={_RETAINED_MARKER_KEY: None},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "lane retention marker clear failed dispatch=%s err=%s", dispatch_id, exc
        )
    try:
        from services.git_integration_worker.cursor_sdk_lane_b_disposition import (
            clear_disposition,
        )

        clear_disposition(branch_name=branch_name)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "lane retention disposition clear failed branch=%s err=%s",
            branch_name,
            exc,
        )


def _retention_walk(
    *,
    dispatch_id: str,
    thread_id: str | None = None,
    closeout_text: str | None = None,
    branch_name: str | None = None,
) -> str | None:
    """Walk ``nest_under`` and return the retain reason, or None when it may settle.

    Raises ``_LedgerReadError`` when a ledger read fails. A missing row or a
    ``branch_name`` that matches no open conductor's lane returns None.
    """
    _ = thread_id
    try:
        row = _load_row(dispatch_id)
    except Exception as exc:  # noqa: BLE001 — callers map this to None
        logger.warning(
            "lane retention: ledger read failed dispatch=%s err=%s", dispatch_id, exc
        )
        raise _LedgerReadError from exc
    if row is None:
        return None
    tokens = closeout_stop_tokens(row, closeout_text)
    if (
        "DONE" in tokens
        and is_conductor_dispatch_row(row)
        and _branch_is_row_lane(row, branch_name)
    ):
        _clear_retained_marker(dispatch_id=dispatch_id, branch_name=branch_name or "")
    reason = conductor_mission_open(row, closeout_text=closeout_text, closing=True)
    if reason and _branch_is_row_lane(row, branch_name):
        _persist_retained_marker(
            dispatch_id=dispatch_id, branch_name=branch_name or "", reason=reason
        )
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
            raise _LedgerReadError from exc
        if parent is None:
            break
        parent_reason = conductor_mission_open(parent, closeout_text=None)
        if parent_reason and _branch_is_row_lane(parent, branch_name):
            nested = f"nested_under_open_conductor:{parent_id}:{parent_reason}"
            _persist_retained_marker(
                dispatch_id=parent_id,
                branch_name=branch_name or "",
                reason=nested,
            )
            return nested
        parent_id = str(_record(parent).get("nest_under") or "").strip()
    if is_conductor_dispatch_row(row):
        return None
    thread_id = str(row.get("thread_id") or "").strip()
    if not thread_id:
        return None
    try:
        conductor_row = _load_latest_terminal_conductor_on_thread(thread_id)
    except Exception as exc:  # noqa: BLE001 — same posture as _load_row
        logger.warning(
            "lane retention: thread conductor read failed thread=%s err=%s",
            thread_id,
            exc,
        )
        raise _LedgerReadError from exc
    if conductor_row is None:
        return None
    reason = conductor_mission_open(conductor_row, closeout_text=None)
    if reason and _branch_is_row_lane(conductor_row, branch_name):
        conductor_id = str(conductor_row.get("dispatch_id") or "")
        _persist_retained_marker(
            dispatch_id=conductor_id,
            branch_name=branch_name or "",
            reason=reason,
        )
        return reason
    return None


def lane_retention_lookup(
    *,
    dispatch_id: str,
    thread_id: str | None = None,
    closeout_text: str | None = None,
    branch_name: str | None = None,
) -> tuple[str | None, bool]:
    """``(reason, lookup_ok)`` for the abandoned-mark path.

    ``lookup_ok`` is False only when a ledger read failed. The reason is then
    None and is not a disposition value: the caller skips the abandoned mark
    and does not treat the failure as retain. A missing row is
    ``(None, True)`` — the read answered, and the branch may settle.
    """
    try:
        reason = _retention_walk(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            closeout_text=closeout_text,
            branch_name=branch_name,
        )
    except _LedgerReadError:
        return None, False
    return reason, True


def lane_retention_reason(
    *,
    dispatch_id: str,
    thread_id: str | None = None,
    closeout_text: str | None = None,
    branch_name: str | None = None,
) -> str | None:
    """Why this lane branch must survive this closeout, or None when it may settle.

    Walks ``nest_under`` from the closing row toward its root. The closing row
    is decided on its stop tokens. A nested child retains only the open
    conductor ancestor's own lane branch; a limb settling ``cursor-sdk/lane-{its
    thread}`` is not that branch and returns None. ``thread_id`` is accepted
    for symmetry with the settlement call sites; the decision rests on ledger
    rows and ``branch_name``. Never raises — a ledger that cannot be read
    settles as before (returns None) and logs a warning. A missing row or a
    ``branch_name`` that matches no open conductor's lane also returns None.
    Callers that must tell a failed read from "may settle" use
    ``lane_retention_lookup``.
    """
    reason, _lookup_ok = lane_retention_lookup(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        closeout_text=closeout_text,
        branch_name=branch_name,
    )
    return reason


__all__ = [
    "RETAINED_FOR_MISSION",
    "closeout_stop_tokens",
    "conductor_mission_open",
    "lane_retention_lookup",
    "lane_retention_reason",
]
