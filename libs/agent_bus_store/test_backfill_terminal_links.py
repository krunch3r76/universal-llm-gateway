"""Hermetic plans for the thread-12286 terminal-link backfill."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from agent_bus_store.backfill_terminal_links import (
    EXCLUDED_EXECUTION_ID,
    THREAD_ID,
    RowPlan,
    apply_backfill,
    plan_backfill,
    render_dry_run,
    survey_backfill,
)

pytestmark = pytest.mark.offline

_SCHEMA = """
CREATE TABLE threads (
    id TEXT PRIMARY KEY,
    bus_lifecycle_state TEXT
);
CREATE TABLE thread_dispatch_links (
    thread_id TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    pipeline_id TEXT NOT NULL,
    terminal_at TEXT,
    terminal_status TEXT,
    delivery_at TEXT,
    last_heartbeat_at TEXT,
    PRIMARY KEY (thread_id, execution_id)
);
CREATE TABLE events (
    signal TEXT NOT NULL,
    execution_id TEXT,
    payload TEXT
);
CREATE TABLE cursor_sdk_dispatches (
    dispatch_id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    execution_id TEXT,
    status TEXT NOT NULL,
    last_heartbeat_at TEXT
);
CREATE TABLE turns (
    id INTEGER PRIMARY KEY,
    thread_id TEXT,
    body TEXT
);
"""


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _thread(
    conn: sqlite3.Connection, thread_id: str, lifecycle: str = "active"
) -> None:
    conn.execute(
        "INSERT INTO threads (id, bus_lifecycle_state) VALUES (?, ?)",
        (thread_id, lifecycle),
    )


def _link(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    execution_id: str,
    pipeline_id: str,
    terminal_status: str | None = None,
    terminal_at: str | None = None,
    last_heartbeat_at: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO thread_dispatch_links ("
        "thread_id, execution_id, pipeline_id, terminal_status, "
        "terminal_at, last_heartbeat_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            thread_id,
            execution_id,
            pipeline_id,
            terminal_status,
            terminal_at,
            last_heartbeat_at,
        ),
    )


def _event(conn: sqlite3.Connection, signal: str, execution_id: str) -> None:
    conn.execute(
        "INSERT INTO events (signal, execution_id, payload) VALUES (?, ?, ?)",
        (signal, execution_id, json.dumps({"execution_id": execution_id})),
    )


def test_sibling_completed_copies_and_bare_row_stays_null() -> None:
    conn = _conn()
    _thread(conn, THREAD_ID)
    _thread(conn, "9001")
    _link(
        conn,
        thread_id=THREAD_ID,
        execution_id="exec-sib",
        pipeline_id="cursor-sdk-generate",
    )
    _link(
        conn,
        thread_id=THREAD_ID,
        execution_id="exec-bare",
        pipeline_id="cursor-sdk-generate",
    )
    _link(
        conn,
        thread_id="9001",
        execution_id="exec-sib",
        pipeline_id="cursor-sdk-generate",
        terminal_status="completed",
        terminal_at="2026-09-01T00:00:00Z",
    )
    plans = plan_backfill(conn, thread_id=THREAD_ID)
    assert len(plans) == 1
    assert plans[0].execution_id == "exec-sib"
    assert plans[0].pipeline_id == "cursor-sdk-generate"
    assert plans[0].terminal_status == "completed"
    assert plans[0].evidence == ("sibling thread_id=9001 terminal_status=completed")
    assert all(plan.execution_id != "exec-bare" for plan in plans)
    text = render_dry_run(
        plans,
        survey_backfill(conn, thread_id=THREAD_ID)[1],
        before=2,
    )
    assert "null_links_12286_before=2" in text
    assert "null_links_12286_after_projected=1" in text
    assert "reason=no_terminal_evidence" in text


def test_disagreeing_siblings_conflict_and_no_plan() -> None:
    conn = _conn()
    _thread(conn, THREAD_ID)
    _thread(conn, "9001")
    _thread(conn, "9002")
    _link(
        conn,
        thread_id=THREAD_ID,
        execution_id="exec-split",
        pipeline_id="cdp-generate",
    )
    _link(
        conn,
        thread_id="9001",
        execution_id="exec-split",
        pipeline_id="cdp-generate",
        terminal_status="completed",
        terminal_at="2026-09-01T00:00:00Z",
    )
    _link(
        conn,
        thread_id="9002",
        execution_id="exec-split",
        pipeline_id="cdp-generate",
        terminal_status="failed",
        terminal_at="2026-09-01T00:00:00Z",
    )
    plans, dispositions = survey_backfill(conn, thread_id=THREAD_ID)
    assert plans == []
    assert [item.kind for item in dispositions] == ["CONFLICT"]
    text = render_dry_run(plans, dispositions, before=1)
    assert "CONFLICT execution_id=exec-split pipeline_id=cdp-generate" in text
    assert "null_links_12286_after_projected=1" in text


def test_proof_and_stalled_events() -> None:
    conn = _conn()
    _thread(conn, THREAD_ID)
    _link(
        conn,
        thread_id=THREAD_ID,
        execution_id="exec-proof",
        pipeline_id="cdp-generate",
    )
    _link(
        conn,
        thread_id=THREAD_ID,
        execution_id="exec-stalled",
        pipeline_id="cdp-generate",
    )
    _link(
        conn,
        thread_id=THREAD_ID,
        execution_id="exec-both",
        pipeline_id="cdp-generate",
    )
    _event(conn, "cdp.generate.proof", "exec-proof")
    _event(conn, "cdp.generate.stalled", "exec-stalled")
    _event(conn, "cdp.generate.proof", "exec-both")
    _event(conn, "cdp.generate.stalled", "exec-both")
    plans = plan_backfill(conn, thread_id=THREAD_ID)
    by_id = {plan.execution_id: plan for plan in plans}
    assert set(by_id) == {"exec-proof", "exec-stalled", "exec-both"}
    assert by_id["exec-proof"].terminal_status == "completed"
    assert by_id["exec-proof"].evidence == "event signal=cdp.generate.proof"
    assert by_id["exec-stalled"].terminal_status == "failed"
    assert by_id["exec-stalled"].evidence == "event signal=cdp.generate.stalled"
    assert by_id["exec-both"].terminal_status == "completed"
    assert by_id["exec-both"].evidence == "event signal=cdp.generate.proof"


def test_chat_dispatch_with_proof_is_not_planned() -> None:
    conn = _conn()
    _thread(conn, THREAD_ID)
    _link(
        conn,
        thread_id=THREAD_ID,
        execution_id="exec-chat",
        pipeline_id="chat-dispatch",
    )
    _event(conn, "cdp.generate.proof", "exec-chat")
    plans, dispositions = survey_backfill(conn, thread_id=THREAD_ID)
    assert plans == []
    assert dispositions[0].kind == "out_of_scope_chat_dispatch"


def test_excluded_execution_with_sibling_is_not_planned() -> None:
    conn = _conn()
    _thread(conn, THREAD_ID)
    _thread(conn, "9001")
    _link(
        conn,
        thread_id=THREAD_ID,
        execution_id=EXCLUDED_EXECUTION_ID,
        pipeline_id="cdp-generate",
    )
    _link(
        conn,
        thread_id="9001",
        execution_id=EXCLUDED_EXECUTION_ID,
        pipeline_id="cdp-generate",
        terminal_status="completed",
        terminal_at="2026-09-01T00:00:00Z",
    )
    plans = plan_backfill(conn, thread_id=THREAD_ID)
    assert plans == []
    _dispositions = survey_backfill(conn, thread_id=THREAD_ID)[1]
    assert _dispositions[0].kind == "excluded_execution"


def test_heartbeat_and_giw_running_are_not_evidence() -> None:
    conn = _conn()
    _thread(conn, THREAD_ID)
    heartbeat = (datetime.now(UTC) - timedelta(minutes=40)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    _link(
        conn,
        thread_id=THREAD_ID,
        execution_id="exec-live",
        pipeline_id="cursor-sdk-generate",
        last_heartbeat_at=heartbeat,
    )
    conn.execute(
        "INSERT INTO cursor_sdk_dispatches ("
        "dispatch_id, thread_id, execution_id, status, last_heartbeat_at"
        ") VALUES (?, ?, ?, ?, ?)",
        ("disp-live", THREAD_ID, "exec-live", "running", heartbeat),
    )
    plans, dispositions = survey_backfill(conn, thread_id=THREAD_ID)
    assert plans == []
    assert dispositions[0].kind == "no_terminal_evidence"


def test_other_thread_is_not_planned() -> None:
    conn = _conn()
    _thread(conn, "9999")
    _thread(conn, "9001")
    _link(
        conn,
        thread_id="9999",
        execution_id="exec-other",
        pipeline_id="cursor-sdk-generate",
    )
    _link(
        conn,
        thread_id="9001",
        execution_id="exec-other",
        pipeline_id="cursor-sdk-generate",
        terminal_status="completed",
        terminal_at="2026-09-01T00:00:00Z",
    )
    assert plan_backfill(conn, thread_id="9999") == []


def test_apply_backfill_does_not_change_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent_bus_store.backfill_terminal_links.now",
        lambda: "2026-09-29T08:30:00Z",
    )
    conn = _conn()
    _thread(conn, THREAD_ID, "active")
    _thread(conn, "9001", "active")
    _link(
        conn,
        thread_id=THREAD_ID,
        execution_id="exec-sib",
        pipeline_id="cursor-sdk-generate",
    )
    _link(
        conn,
        thread_id="9001",
        execution_id="exec-sib",
        pipeline_id="cursor-sdk-generate",
        terminal_status="completed",
        terminal_at="2026-09-01T00:00:00Z",
    )
    plans = plan_backfill(conn, thread_id=THREAD_ID)
    updated = apply_backfill(conn, plans)
    assert updated == 1
    lifecycle = conn.execute(
        "SELECT bus_lifecycle_state FROM threads WHERE id = ?",
        (THREAD_ID,),
    ).fetchone()
    assert lifecycle[0] == "active"
    stamped = conn.execute(
        "SELECT terminal_status, terminal_at, delivery_at "
        "FROM thread_dispatch_links WHERE thread_id = ? AND execution_id = ?",
        (THREAD_ID, "exec-sib"),
    ).fetchone()
    assert stamped[0] == "completed"
    assert stamped[1] == "2026-09-29T08:30:00Z"
    assert stamped[2] == "2026-09-29T08:30:00Z"
    sibling = conn.execute(
        "SELECT terminal_at FROM thread_dispatch_links "
        "WHERE thread_id = '9001' AND execution_id = 'exec-sib'"
    ).fetchone()
    assert sibling[0] == "2026-09-01T00:00:00Z"
    assert conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 0
    assert apply_backfill(conn, plans) == 0


def test_apply_refuses_excluded_execution() -> None:
    conn = _conn()
    _thread(conn, THREAD_ID)
    _link(
        conn,
        thread_id=THREAD_ID,
        execution_id=EXCLUDED_EXECUTION_ID,
        pipeline_id="cdp-generate",
    )
    plan = RowPlan(
        execution_id=EXCLUDED_EXECUTION_ID,
        pipeline_id="cdp-generate",
        terminal_status="completed",
        evidence="sibling thread_id=9001 terminal_status=completed",
    )
    with pytest.raises(ValueError, match="excluded execution"):
        apply_backfill(conn, [plan])
    status = conn.execute(
        "SELECT terminal_status FROM thread_dispatch_links WHERE execution_id = ?",
        (EXCLUDED_EXECUTION_ID,),
    ).fetchone()
    assert status[0] is None
