"""One read of every conductor mission: state, stop, reason, time stopped, release.

``manage busy_status`` shows only GIW's live holders; a parked or waiting
mission is invisible until someone searches threads (friction class A10,
2026-10-01). This module folds the dispatch ledger into one row per mission
(``work_key`` × worker ``thread_id``): the mission's latest conductor row,
the state that row left the mission in, why, how long it has been there, and
the exact call that releases it. Everything is derived from ledger columns and
``record_json`` stamps that the hop reactor, park gate and park harvest
already write (``hop_parked`` / ``hop_park_reason`` / ``hop_park_released_at``,
``closeout_stop_tokens``, ``hop_successor``, ``hop_admit_error``,
``park_kind`` / ``park_resumed_by``, ``closeout_turn``,
``consult_summoning_after_turn``, ``hop_park_harvest_fired_at``). Read-only;
never raises on a malformed record.

Callers: ``scripts/conductor-census`` (table or JSON) and any seat that wants
a census payload. The vocabulary of ``MissionCensusRow.state`` is
``CENSUS_STATES``.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

CENSUS_STATES: tuple[str, ...] = (
    "live",
    "nested_wait",
    "restart_parked",
    "budget_parked",
    "done",
    "succeeded",
    "hold_merge",
    "operator_gate",
    "pinned",
    "consult_pending",
    "transport_parked",
    "hop_owed",
    "crashed",
    "silent",
)

_LIVE = frozenset({"queued", "admitted", "running"})
_TERMINAL = frozenset({"completed", "failed", "cancelled"})


@dataclass(frozen=True, slots=True)
class MissionCensusRow:
    """One mission as the ledger shows it right now.

    ``since`` is the ISO instant the state began (the latest row's terminal
    time, else its start); ``seconds_in_state`` is measured from ``now`` at
    census time. ``release`` is the literal call, or the event, that moves
    the mission on — ``none`` when nothing is owed.
    """

    work_key: str
    thread_id: str
    summoning_thread_id: str | None
    dispatch_id: str
    status: str
    state: str
    stop: str
    reason: str
    since: str | None
    seconds_in_state: float | None
    successor: str | None
    release: str


def _record(row: dict[str, Any]) -> dict[str, Any]:
    raw = str(row.get("record_json") or "")
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _tokens(record: dict[str, Any]) -> frozenset[str]:
    raw = record.get("closeout_stop_tokens")
    if isinstance(raw, list):
        return frozenset(str(t).upper() for t in raw)
    return frozenset()


def _parse_instant(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _admit_call(
    *, work_key: str, thread_id: str, extra: str = ""
) -> str:
    tail = f", {extra}" if extra else ""
    return (
        'team_dispatch(op="generate", seat="cursor-sdk", contract="conductor", '
        f'source_ref="{work_key}", lane="B", reuse_thread="{thread_id}", '
        f'dispatch_thread_id="{thread_id}"{tail})'
    )


def classify_mission_row(
    row: dict[str, Any], *, now: datetime | None = None
) -> MissionCensusRow:
    """Fold one latest conductor ledger row into its census row.

    The order of the checks is the order the substrate itself consults them:
    a live or nested-waiting row first; a GIW restart park; a hop-budget park
    (which ``park_harvest_owed`` excludes, so it only ever releases by hand);
    then the designed stop tokens; then a planned hop with no successor; then
    a terminal row with no token at all (crash or silent exit).
    """
    now_dt = now or datetime.now(UTC)
    record = _record(row)
    work_key = str(row.get("work_key") or row.get("source_ref") or "")
    thread_id = str(row.get("thread_id") or "")
    summoning = str(record.get("summoning_thread_id") or "").strip() or None
    dispatch_id = str(row.get("dispatch_id") or "")
    status = str(row.get("status") or "")
    tokens = _tokens(record)
    stop = ",".join(sorted(tokens))
    successor = str(record.get("hop_successor") or "").strip() or None
    terminal_at = row.get("terminal_at")
    started_at = row.get("started_at") or row.get("queued_at")
    since_raw = terminal_at if status in _TERMINAL else started_at
    since_dt = _parse_instant(since_raw)
    seconds = (now_dt - since_dt).total_seconds() if since_dt else None
    since = since_dt.isoformat() if since_dt else None

    def _row(state: str, reason: str, release: str) -> MissionCensusRow:
        return MissionCensusRow(
            work_key=work_key,
            thread_id=thread_id,
            summoning_thread_id=summoning,
            dispatch_id=dispatch_id,
            status=status,
            state=state,
            stop=stop,
            reason=reason,
            since=since,
            seconds_in_state=seconds,
            successor=successor,
            release=release,
        )

    resume = _admit_call(
        work_key=work_key, thread_id=thread_id, extra=f'resume_of="{dispatch_id}"'
    )

    if status in _LIVE:
        return _row("live", f"dispatch {status}", "none (running)")
    if status == "parked_waiting":
        return _row(
            "nested_wait",
            "parent parked while a nested child runs",
            "none (the nest closing resumes the parent)",
        )
    if row.get("park_kind") == "park_for_restart" and not row.get("park_resumed_by"):
        return _row(
            "restart_parked",
            f"GIW park_for_restart (intent {row.get('park_intent_id') or '?'}); "
            "resume child not yet admitted",
            f"GIW resumes it after the restart drains; else {resume}",
        )
    if record.get("hop_parked") is True and not record.get("hop_park_released_at"):
        reason = str(record.get("hop_park_reason") or "hop_parked")
        return _row(
            "budget_parked",
            f"hop budget park: {reason} (park_harvest_owed excludes it; hand release only)",
            _admit_call(
                work_key=work_key,
                thread_id=thread_id,
                extra='generation_options={"hop_park_release": true}',
            ),
        )
    if "DONE" in tokens:
        return _row("done", "mission closed by its conductor", "none")
    if successor:
        return _row(
            "succeeded",
            f"successor {successor} admitted",
            "none (follow the successor row)",
        )
    if "HOLD_MERGE" in tokens:
        return _row(
            "hold_merge",
            "conductor holds the merge for a named operator decision",
            f"operator decides the merge on thread {thread_id}; then {resume}",
        )
    if "OPERATOR_GATE" in tokens or "CONFIRM_PENDING" in tokens:
        return _row(
            "operator_gate",
            "designed operator-only stop",
            f"answer on thread {thread_id}; then {resume}",
        )
    if "ROW_PINNED" in tokens:
        return _row(
            "pinned",
            "row pinned until an explicit gate",
            f"clear the gate on thread {thread_id}; then {resume}",
        )
    if "CONSULT_PENDING" in tokens:
        closeout_turn = record.get("closeout_turn")
        watermark = record.get("consult_summoning_after_turn")
        where = f"thread {thread_id} after turn {closeout_turn}"
        if summoning and isinstance(watermark, int):
            where += f", or summoning {summoning} after turn {watermark}"
        return _row(
            "consult_pending",
            f"waiting for a web-anthropic reply on {where}",
            "a web-anthropic reply there; the watchdog admits the successor "
            f"(consult_pending_continue); else {resume}",
        )
    if "PARKED_TRANSPORT" in tokens:
        armed = record.get("hop_park_harvest_fired_at")
        harvest_owed = record.get("closeout_harvest_owed") is True
        if harvest_owed:
            closeout_turn = record.get("closeout_turn")
            where = f"thread {thread_id} after turn {closeout_turn}"
            return _row(
                "transport_parked",
                "CDP consult owed; "
                + ("harvest watcher armed on the summoning thread" if armed else "harvest not yet armed"),
                f"a web-anthropic reply on {where} (park_harvest_continue); else {resume}",
            )
        return _row(
            "transport_parked",
            "PARKED_TRANSPORT with no harvest owed",
            resume,
        )
    if "ROW_HOP" in tokens:
        admit_error = record.get("hop_admit_error")
        if isinstance(admit_error, dict):
            err = admit_error.get("last_error") or admit_error.get("error") or ""
            code = admit_error.get("last_status_code") or admit_error.get("status_code")
            return _row(
                "hop_owed",
                f"successor admit refused ({code}): {str(err)[:160]}",
                f"fix the refusal, then the watchdog retries; else {resume}",
            )
        return _row(
            "hop_owed",
            "ROW_HOP closed with no successor admitted yet",
            f"hop reactor / watchdog admits the successor; else {resume}",
        )
    if status == "failed":
        return _row(
            "crashed",
            "terminal failed with no designed stop token",
            f"watchdog crash-resume (crash cap applies); else {resume}",
        )
    return _row(
        "silent",
        f"terminal {status or 'unknown'} with no designed stop token",
        f"watchdog re-admits (budgeted); else {resume}",
    )


def latest_mission_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Latest conductor ledger row per (work_key, thread_id), oldest mission first."""
    cursor = conn.execute(
        "SELECT * FROM cursor_sdk_dispatches WHERE lower(contract)='conductor' "
        "ORDER BY COALESCE(terminal_at, started_at, queued_at)"
    )
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in cursor.fetchall():
        row = {k: raw[k] for k in raw.keys()}
        key = (
            str(row.get("work_key") or row.get("source_ref") or ""),
            str(row.get("thread_id") or ""),
        )
        latest[key] = row
    return list(latest.values())


def census(
    conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
    open_only: bool = False,
) -> list[MissionCensusRow]:
    """Every conductor mission the ledger knows, one row each, newest state first.

    ``open_only`` drops ``done`` and ``succeeded`` rows so the read is only
    what still owes something. Rows that fail to classify never abort the
    census: they fall back to the ``silent`` state with the exception text.
    """
    rows: list[MissionCensusRow] = []
    for raw in latest_mission_rows(conn):
        try:
            entry = classify_mission_row(raw, now=now)
        except Exception as exc:  # noqa: BLE001 — a census must not die on one row
            entry = MissionCensusRow(
                work_key=str(raw.get("work_key") or ""),
                thread_id=str(raw.get("thread_id") or ""),
                summoning_thread_id=None,
                dispatch_id=str(raw.get("dispatch_id") or ""),
                status=str(raw.get("status") or ""),
                state="silent",
                stop="",
                reason=f"unclassifiable row: {exc}",
                since=None,
                seconds_in_state=None,
                successor=None,
                release="inspect the ledger row",
            )
        if open_only and entry.state in {"done", "succeeded"}:
            continue
        rows.append(entry)
    # Longest-stuck first; rows with no measurable age last.
    rows.sort(
        key=lambda r: -(r.seconds_in_state if r.seconds_in_state is not None else -1.0)
    )
    return rows


def _fmt_age(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    if seconds < 90:
        return f"{int(seconds)}s"
    if seconds < 5400:
        return f"{seconds / 60:.0f}m"
    if seconds < 172800:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def render_table(rows: list[MissionCensusRow]) -> str:
    """Fixed-width text, one mission per line, release call last so it can be copied."""
    header = f"{'mission':<44} {'worker':<7} {'state':<17} {'stop':<16} {'age':>6}  reason / release"
    lines = [header, "-" * len(header)]
    for r in rows:
        lines.append(
            f"{r.work_key[:44]:<44} {r.thread_id:<7} {r.state:<17} "
            f"{(r.stop or '-')[:16]:<16} {_fmt_age(r.seconds_in_state):>6}  {r.reason}"
        )
        lines.append(f"{'':<44} {'':<7} {'':<17} {'':<16} {'':>6}  → {r.release}")
    if not rows:
        lines.append("(no conductor missions in the ledger)")
    return "\n".join(lines)


def render_json(rows: list[MissionCensusRow]) -> str:
    """JSON array of census rows, stable key order, for seats that parse."""
    return json.dumps([asdict(r) for r in rows], indent=2, sort_keys=True)


__all__ = [
    "CENSUS_STATES",
    "MissionCensusRow",
    "census",
    "classify_mission_row",
    "latest_mission_rows",
    "render_json",
    "render_table",
]
