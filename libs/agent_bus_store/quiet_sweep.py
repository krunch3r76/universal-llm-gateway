"""Watchdog I/O for the quiet-with-WIP gate (A′ — arc 6885).

Gathers bus rows into ``QuietWithWipSnapshot``, evaluates the pure predicate,
and soft-actuates: alarm row + event + lane turn (WAKE-relay consumer). Kept
separate from ``quiet_with_wip`` so the predicate stays I/O-free.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import UTC, datetime

from claude_bundles.cdp_registry_store import load_sessions
from claude_bundles.cse_session_common import is_parked_body
from claude_bundles.cse_session_obligations import (
    get_open_stop_ack_owed_for_execution,
    get_open_wake_owed,
)

from .db.connection import connect, now
from .db.turns import insert_turn
from .events.quiet_with_wip import emit_quiet_with_wip_fired
from .quiet_with_wip import (
    DispatchLinkView,
    LaneTurnView,
    QuietWithWipSnapshot,
    evaluate_quiet_with_wip,
    infer_seat,
    parse_iso_ts,
)

logger = logging.getLogger("agent-bus.quiet-sweep")

_QUIET_THRESHOLD_S: float = float(
    os.getenv("AGENT_BUS_QUIET_WITH_WIP_THRESHOLD_S", "600")
)


def _licensed_park(thread_id: str, links: list[DispatchLinkView], turns: list[LaneTurnView]) -> bool:
    """True when wake_owed / stop_ack_owed is open or latest seat body is PARKED."""
    try:
        sessions = load_sessions()
        if get_open_wake_owed(sessions, thread=thread_id) is not None:
            return True
    except Exception as exc:  # noqa: BLE001 — park probe must not kill sweep
        logger.debug("wake_owed probe failed for %s: %s", thread_id, exc)
    for lnk in links:
        try:
            if get_open_stop_ack_owed_for_execution(lnk.execution_id) is not None:
                return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("stop_ack probe failed for %s: %s", lnk.execution_id, exc)
    seat = infer_seat(turns)
    if seat:
        seat_turns = [t for t in turns if t.from_agent == seat]
        if seat_turns:
            latest = max(seat_turns, key=lambda t: t.turn_number)
            if is_parked_body(latest.body):
                return True
    return False


def _alarm_open(thread_id: str) -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM thread_quiet_alarms "
            "WHERE thread_id = ? AND status = 'open' LIMIT 1",
            (thread_id,),
        ).fetchone()
    return row is not None


def _load_lane(thread_id: str) -> tuple[str, list[DispatchLinkView], list[LaneTurnView]] | None:
    with connect() as conn:
        trow = conn.execute(
            "SELECT bus_lifecycle_state FROM threads WHERE id = ?",
            (thread_id,),
        ).fetchone()
        if trow is None:
            return None
        lifecycle = trow["bus_lifecycle_state"] or ""
        link_rows = conn.execute(
            "SELECT execution_id, terminal_status, terminal_at "
            "FROM thread_dispatch_links WHERE thread_id = ?",
            (thread_id,),
        ).fetchall()
        turn_rows = conn.execute(
            "SELECT turn_number, from_agent, created_at, subject, body "
            "FROM turns WHERE thread = ? ORDER BY turn_number ASC",
            (thread_id,),
        ).fetchall()
    links = [
        DispatchLinkView(
            execution_id=r["execution_id"],
            terminal_status=r["terminal_status"],
            terminal_at=r["terminal_at"],
        )
        for r in link_rows
    ]
    turns = [
        LaneTurnView(
            turn_number=int(r["turn_number"]),
            from_agent=str(r["from_agent"]),
            created_at=str(r["created_at"]),
            subject=str(r["subject"] or ""),
            body=str(r["body"] or ""),
        )
        for r in turn_rows
    ]
    return lifecycle, links, turns


def _candidate_thread_ids() -> list[str]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id FROM threads "
            "WHERE bus_lifecycle_state IN ('admitted', 'active') "
            "ORDER BY updated_at ASC"
        ).fetchall()
    return [str(r["id"]) for r in rows]


def _write_alarm_and_actuate(
    *,
    thread_id: str,
    seat: str,
    reason: str,
    wip_execution_ids: tuple[str, ...],
) -> str:
    """Persist alarm, emit event, post WAKE-relay turn. Returns alarm_id."""
    alarm_id = f"qwa-{uuid.uuid4().hex[:12]}"
    fired_at = now()
    with connect() as conn:
        conn.execute(
            "INSERT INTO thread_quiet_alarms ("
            "  alarm_id, thread_id, seat, first_seen_at, fired_at, "
            "  reason, wip_execution_ids, status"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, 'open')",
            (
                alarm_id,
                thread_id,
                seat,
                fired_at,
                fired_at,
                reason,
                json.dumps(list(wip_execution_ids)),
            ),
        )
    emit_quiet_with_wip_fired(
        thread=thread_id,
        seat=seat,
        reason=reason,  # type: ignore[arg-type]
        alarm_id=alarm_id,
        wip_execution_ids=list(wip_execution_ids),
    )
    ids_txt = ", ".join(wip_execution_ids) if wip_execution_ids else "(none)"
    body = (
        f"Quiet with work in flight — seat={seat} reason={reason} "
        f"execution_ids=[{ids_txt}] alarm_id={alarm_id}. "
        "Harvest or act; silence with WIP is the defect this turn names."
    )
    insert_turn(
        thread=thread_id,
        from_agent="dispatch",
        to_agent=seat,
        subject="Quiet with work in flight",
        body=body,
        after_turn=None,
    )
    return alarm_id


def sweep_quiet_with_wip(*, threshold_s: float | None = None) -> int:
    """Evaluate admitted/active lanes; fire at most one open alarm per lane.

    Returns the number of new alarms written this pass.
    """
    thresh = float(threshold_s if threshold_s is not None else _QUIET_THRESHOLD_S)
    fired = 0
    now_dt = datetime.now(UTC)
    for thread_id in _candidate_thread_ids():
        loaded = _load_lane(thread_id)
        if loaded is None:
            continue
        lifecycle, links, turns = loaded
        seat = infer_seat(turns)
        if seat is None:
            continue
        if _alarm_open(thread_id):
            continue
        snap = QuietWithWipSnapshot(
            thread_id=thread_id,
            seat=seat,
            now=now_dt,
            threshold_s=thresh,
            lifecycle=lifecycle,
            links=tuple(links),
            turns=tuple(turns),
            licensed_park=_licensed_park(thread_id, links, turns),
            alarm_open=False,
        )
        verdict = evaluate_quiet_with_wip(snap)
        if not verdict.fire or verdict.reason is None:
            continue
        _write_alarm_and_actuate(
            thread_id=thread_id,
            seat=seat,
            reason=verdict.reason,
            wip_execution_ids=verdict.wip_execution_ids,
        )
        fired += 1
    return fired


def discharge_quiet_alarms_on_seat_turn(
    *,
    thread_id: str,
    from_agent: str,
    created_at: str | None = None,
) -> int:
    """Discharge open alarms when the quiet seat speaks again (hook helper).

    Not wired into insert_turn in this land — available for a follow-up join.
    Returns count discharged.
    """
    del created_at  # reserved for finer discharge rules
    with connect() as conn:
        cur = conn.execute(
            "UPDATE thread_quiet_alarms SET status = 'discharged', "
            "discharged_at = ? "
            "WHERE thread_id = ? AND seat = ? AND status = 'open'",
            (now(), thread_id, from_agent),
        )
        return int(cur.rowcount or 0)


# Re-export for tests that build snapshots from DB timestamps.
__all__ = [
    "discharge_quiet_alarms_on_seat_turn",
    "parse_iso_ts",
    "sweep_quiet_with_wip",
]
