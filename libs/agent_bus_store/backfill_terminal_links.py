"""One-shot stamp of null dispatch links from a sibling row or a CDP event.

Evidence order, first hit wins: agreeing sibling ``completed``/``failed``,
then ``cdp.generate.proof`` (value ``completed``), then ``cdp.generate.stalled``
(value ``failed``). Disagreeing siblings are ``CONFLICT`` and are not planned.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from agent_bus_store.db.connection import now

THREAD_ID = "12286"
EXCLUDED_EXECUTION_ID = "662daf5d-c192-46fa-bec3-066aa4284f1a"
_PROOF_SIGNAL = "cdp.generate.proof"
_STALLED_SIGNAL = "cdp.generate.stalled"
_TERMINAL_STATUSES = ("completed", "failed")

_UPDATE_SQL = (
    "UPDATE thread_dispatch_links "
    "SET terminal_status = ?, terminal_at = ?, delivery_at = ? "
    "WHERE thread_id = ? AND execution_id = ? AND terminal_status IS NULL"
)


@dataclass(frozen=True)
class RowPlan:
    """One null link that a stored terminal fact can stamp."""

    execution_id: str
    pipeline_id: str
    terminal_status: str
    evidence: str
    thread_id: str = THREAD_ID


@dataclass(frozen=True)
class RowDisposition:
    """A null link that is not a plan."""

    execution_id: str
    pipeline_id: str
    kind: str


def plan_backfill(conn: sqlite3.Connection, *, thread_id: str) -> list[RowPlan]:
    """Return stampable rows for ``thread_id``. Only ``12286`` is eligible."""
    plans, _dispositions = survey_backfill(conn, thread_id=thread_id)
    return plans


def survey_backfill(
    conn: sqlite3.Connection, *, thread_id: str
) -> tuple[list[RowPlan], list[RowDisposition]]:
    """Classify null links. Non-``12286`` threads yield nothing."""
    if thread_id != THREAD_ID:
        return [], []
    plans: list[RowPlan] = []
    dispositions: list[RowDisposition] = []
    rows = conn.execute(
        "SELECT execution_id, pipeline_id FROM thread_dispatch_links "
        "WHERE thread_id = ? AND terminal_status IS NULL "
        "ORDER BY execution_id",
        (thread_id,),
    ).fetchall()
    for row in rows:
        execution_id = str(row[0])
        pipeline_id = str(row[1])
        if execution_id == EXCLUDED_EXECUTION_ID:
            dispositions.append(
                RowDisposition(execution_id, pipeline_id, "excluded_execution")
            )
            continue
        if pipeline_id == "chat-dispatch":
            dispositions.append(
                RowDisposition(execution_id, pipeline_id, "out_of_scope_chat_dispatch")
            )
            continue
        decision = _sibling_decision(conn, execution_id, thread_id)
        if decision is not None:
            status, evidence = decision
            if status == "CONFLICT":
                dispositions.append(
                    RowDisposition(execution_id, pipeline_id, "CONFLICT")
                )
                continue
            plans.append(
                RowPlan(
                    execution_id=execution_id,
                    pipeline_id=pipeline_id,
                    terminal_status=status,
                    evidence=evidence,
                    thread_id=thread_id,
                )
            )
            continue
        if _event_hit(conn, execution_id, _PROOF_SIGNAL):
            plans.append(
                RowPlan(
                    execution_id=execution_id,
                    pipeline_id=pipeline_id,
                    terminal_status="completed",
                    evidence=f"event signal={_PROOF_SIGNAL}",
                    thread_id=thread_id,
                )
            )
            continue
        if _event_hit(conn, execution_id, _STALLED_SIGNAL):
            plans.append(
                RowPlan(
                    execution_id=execution_id,
                    pipeline_id=pipeline_id,
                    terminal_status="failed",
                    evidence=f"event signal={_STALLED_SIGNAL}",
                    thread_id=thread_id,
                )
            )
            continue
        dispositions.append(
            RowDisposition(execution_id, pipeline_id, "no_terminal_evidence")
        )
    return plans, dispositions


def apply_backfill(conn: sqlite3.Connection, plans: list[RowPlan]) -> int:
    """Stamp planned rows. Returns the number of rows updated."""
    refusal = write_allowed(plans)
    if refusal is not None:
        raise ValueError(refusal)
    if not plans:
        return 0
    ts = now()
    # Survey SELECTs leave a deferred transaction open on the default
    # isolation level. Close it so the stamp can take a write lock.
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        updated = 0
        for plan in plans:
            cur = conn.execute(
                _UPDATE_SQL,
                (
                    plan.terminal_status,
                    ts,
                    ts,
                    plan.thread_id,
                    plan.execution_id,
                ),
            )
            updated += int(cur.rowcount)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return updated


def write_allowed(plans: list[RowPlan]) -> str | None:
    """Refusal reason for ``--write``, or None when the plan list may be applied."""
    for plan in plans:
        if plan.execution_id == EXCLUDED_EXECUTION_ID:
            return "excluded execution in plans"
        if plan.thread_id != THREAD_ID:
            return f"thread_id {plan.thread_id}"
        if plan.terminal_status not in _TERMINAL_STATUSES:
            return f"terminal_status {plan.terminal_status}"
        if not plan.evidence.strip():
            return f"empty evidence for {plan.execution_id}"
    return None


def count_null_links(conn: sqlite3.Connection, thread_id: str) -> int:
    """Count null ``terminal_status`` rows on ``thread_id``."""
    row = conn.execute(
        "SELECT COUNT(*) FROM thread_dispatch_links "
        "WHERE thread_id = ? AND terminal_status IS NULL",
        (thread_id,),
    ).fetchone()
    return int(row[0])


def render_dry_run(
    plans: list[RowPlan],
    dispositions: list[RowDisposition],
    *,
    before: int,
) -> str:
    """Text of one dry-run: plan lines, CONFLICT, skips, and the two counts."""
    lines: list[str] = []
    for plan in plans:
        lines.append(
            f"PLAN execution_id={plan.execution_id} "
            f"pipeline_id={plan.pipeline_id} "
            f"terminal_status={plan.terminal_status} "
            f"evidence={plan.evidence}"
        )
    for item in dispositions:
        if item.kind == "CONFLICT":
            lines.append(
                f"CONFLICT execution_id={item.execution_id} "
                f"pipeline_id={item.pipeline_id}"
            )
        else:
            lines.append(
                f"SKIP execution_id={item.execution_id} "
                f"pipeline_id={item.pipeline_id} reason={item.kind}"
            )
    projected = before - len(plans)
    lines.append(f"null_links_12286_before={before}")
    lines.append(f"null_links_12286_after_projected={projected}")
    return "\n".join(lines) + "\n"


def _sibling_decision(
    conn: sqlite3.Connection, execution_id: str, thread_id: str
) -> tuple[str, str] | None:
    """Agreeing sibling status plus evidence, ``CONFLICT``, or no sibling."""
    rows = conn.execute(
        "SELECT thread_id, terminal_status FROM thread_dispatch_links "
        "WHERE execution_id = ? AND thread_id != ? "
        "AND terminal_status IN ('completed', 'failed') "
        "ORDER BY thread_id",
        (execution_id, thread_id),
    ).fetchall()
    if not rows:
        return None
    statuses = {str(row[1]) for row in rows}
    if len(statuses) != 1:
        return ("CONFLICT", "")
    status = str(rows[0][1])
    sibling_thread = str(rows[0][0])
    evidence = f"sibling thread_id={sibling_thread} terminal_status={status}"
    return (status, evidence)


def _has_events(conn: sqlite3.Connection) -> bool:
    for master in ("sqlite_temp_master", "sqlite_master"):
        row = conn.execute(
            f"SELECT 1 FROM {master} WHERE name = 'events' "
            "AND type IN ('table', 'view')"
        ).fetchone()
        if row is not None:
            return True
    return False


def _event_hit(conn: sqlite3.Connection, execution_id: str, signal: str) -> bool:
    """True when an events row stores ``signal`` for this execution.

    The execution id column is the generated ``json_extract(payload,
    '$.execution_id')`` on the event store, so the lookup stays indexed.
    """
    if not _has_events(conn):
        return False
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(events)")}
    if "execution_id" in columns:
        sql = "SELECT 1 FROM events WHERE signal = ? AND execution_id = ? LIMIT 1"
        params: tuple[str, ...] = (signal, execution_id)
    else:
        sql = (
            "SELECT 1 FROM events WHERE signal = ? "
            "AND json_extract(payload, '$.execution_id') = ? LIMIT 1"
        )
        params = (signal, execution_id)
    return conn.execute(sql, params).fetchone() is not None


def _iter_procs() -> Iterator[tuple[int, list[str], dict[str, str]]]:
    proc = Path("/proc")
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmd = [
                part.decode("utf-8", "replace")
                for part in (entry / "cmdline").read_bytes().split(b"\0")
                if part
            ]
            if not cmd:
                continue
            env_raw = (entry / "environ").read_bytes()
            cwd = os.readlink(entry / "cwd")
        except OSError:
            continue
        env: dict[str, str] = {}
        for item in env_raw.split(b"\0"):
            if not item or b"=" not in item:
                continue
            key, value = item.split(b"=", 1)
            env[key.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
        env["PWD"] = cwd
        yield int(entry.name), cmd, env


_FLEET_QUERY_SOCK = "/tmp/universal-protocol/events-query.sock"


def _resolve_agent_bus_db() -> str:
    """Database path from the running agent_bus process environment."""
    found: set[str] = set()
    for _pid, cmd, env in _iter_procs():
        if not any(arg == "agent_bus_store.server:app" for arg in cmd):
            continue
        raw = env.get("AGENT_BUS_DB_PATH", "").strip()
        if not raw:
            continue
        path = _abs_path(raw, env.get("PWD", ""), env.get("HOME", ""))
        found.add(path)
    if len(found) != 1:
        raise SystemExit(
            f"expected one AGENT_BUS_DB_PATH on agent_bus, found {sorted(found)}"
        )
    return next(iter(found))


def _resolve_events_db() -> str:
    """``--db`` of the event_store that serves the fleet query socket.

    Container stores bridge into that socket. ``terminal_event_exists`` reads
    the fleet store, so proof and stalled rows come from there. ``~`` expands
    with that process's ``HOME``, not this dispatch's home.
    """
    found: set[str] = set()
    for _pid, cmd, env in _iter_procs():
        if "event_store" not in cmd or "serve" not in cmd or "--db" not in cmd:
            continue
        if "--query-sock" not in cmd:
            continue
        if cmd[cmd.index("--query-sock") + 1] != _FLEET_QUERY_SOCK:
            continue
        raw = cmd[cmd.index("--db") + 1]
        path = _abs_path(raw, env.get("PWD", ""), env.get("HOME", ""))
        found.add(path)
    if len(found) != 1:
        raise SystemExit(f"expected one fleet event_store --db, found {sorted(found)}")
    return next(iter(found))


def _abs_path(raw: str, cwd: str, home: str) -> str:
    if raw == "~":
        expanded = Path(home) if home else Path(raw)
    elif raw.startswith("~/"):
        expanded = (Path(home) / raw[2:]) if home else Path(raw)
    else:
        expanded = Path(raw)
    if not expanded.is_absolute():
        expanded = Path(cwd) / expanded
    return str(expanded)


def _open_bus(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _attach_events(conn: sqlite3.Connection, events_path: str) -> None:
    conn.execute("ATTACH DATABASE ? AS evdb", (events_path,))
    conn.execute(
        "CREATE TEMP VIEW events AS "
        "SELECT signal, execution_id, payload FROM evdb.events"
    )


def main(argv: list[str] | None = None) -> int:
    """Dry-run by default. ``--write`` applies only when the hard stop passes."""
    parser = argparse.ArgumentParser(prog="agent_bus_store.backfill_terminal_links")
    parser.add_argument("--thread-id", required=True)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Apply the dry-run plans. Default is dry-run.",
    )
    args = parser.parse_args(argv)
    if args.thread_id != THREAD_ID:
        print(f"refused thread_id={args.thread_id}", file=sys.stderr)
        return 2
    bus_path = _resolve_agent_bus_db()
    events_path = _resolve_events_db()
    if not Path(bus_path).is_file():
        print(f"agent bus db missing: {bus_path}", file=sys.stderr)
        return 2
    if not Path(events_path).is_file():
        print(f"events db missing: {events_path}", file=sys.stderr)
        return 2
    print(f"agent_bus_db={bus_path}")
    print(f"events_db={events_path}")
    conn = _open_bus(bus_path)
    try:
        try:
            _attach_events(conn, events_path)
        except sqlite3.Error as exc:
            print(f"events attach failed: {exc}", file=sys.stderr)
            return 2
        plans, dispositions = survey_backfill(conn, thread_id=args.thread_id)
        before = count_null_links(conn, args.thread_id)
        sys.stdout.write(render_dry_run(plans, dispositions, before=before))
        if not args.write:
            return 0
        refusal = write_allowed(plans)
        if refusal is not None:
            print(f"HARD STOP {refusal}", file=sys.stderr)
            return 2
        updated = apply_backfill(conn, plans)
    finally:
        conn.close()
    recount = _open_bus(bus_path)
    try:
        after = count_null_links(recount, args.thread_id)
    finally:
        recount.close()
    print(f"rows_updated={updated}")
    print(f"null_links_12286_after={after}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
