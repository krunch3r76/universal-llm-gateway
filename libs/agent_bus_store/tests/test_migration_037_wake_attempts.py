"""migration_037 idempotence and endpoint_wake_attempt shadow rows."""

from __future__ import annotations

import pytest
from agent_bus_store.db import create_thread, init_db, insert_turn
from agent_bus_store.db.connection import connect, write_connect
from agent_bus_store.db.endpoint_wake_ledger import claim_due_wakes, record_wake_result
from agent_bus_store.db.migrations import run_migrations

pytestmark = pytest.mark.offline


@pytest.fixture()
def db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.delenv("AGENT_BUS_ENDPOINT_WAKE_DISPATCHER", raising=False)
    init_db()


def test_migration_037_idempotent_twice(db: None) -> None:
    with connect() as conn:
        run_migrations(conn)
        run_migrations(conn)
        tables = {
            str(r[0])
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "endpoint_wake_attempt" in tables
        cols = {
            str(r[1]) for r in conn.execute("PRAGMA table_info(endpoint_wake_ledger)")
        }
        for col in (
            "enqueued_at",
            "delivered_at",
            "dropped_at",
            "drop_reason",
            "last_failure_reason",
            "surfaced_at",
        ):
            assert col in cols


def test_read_after_failed_attempt_not_already_read(db: None) -> None:
    detail = create_thread(thread_id=None, slug="read-after-fail")
    assert detail is not None
    thread = str(detail["id"])
    turn_id, _ts, number, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="WAKE|t",
        body="body",
    )
    with write_connect() as conn:
        conn.execute("UPDATE turns SET read_at=datetime('now') WHERE id=?", (turn_id,))
        conn.execute(
            "INSERT INTO endpoint_wake_ledger "
            "(agent, thread_id, turn_id, turn_number, state, reason, created_at, "
            "wake_attempt_count, last_failure_reason, enqueued_at) "
            "VALUES (?,?,?,?, 'pending', 'composer_not_empty', datetime('now'), 1, "
            "'composer_not_empty', datetime('now'))",
            ("grok-bot-gc1", thread, turn_id, number),
        )
    skipped = claim_due_wakes(now="2030-01-01 00:00:00")
    assert skipped.get("wake_id") is None
    rows = skipped.get("rows") or []
    assert len(rows) == 1
    assert rows[0]["reason"] == "read_after_failed_attempt"
    assert rows[0]["state"] == "skipped"


def test_record_wake_result_writes_attempt_row(db: None) -> None:
    detail = create_thread(thread_id=None, slug="attempt-row")
    assert detail is not None
    thread = str(detail["id"])
    turn_id, _ts, number, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="WAKE|t2",
        body="body",
    )
    with write_connect() as conn:
        conn.execute(
            "INSERT INTO endpoint_wake_ledger "
            "(agent, thread_id, turn_id, turn_number, state, wake_id, reason, "
            "created_at, claimed_at) VALUES (?,?,?,?, 'claimed', ?, NULL, "
            "datetime('now'), datetime('now'))",
            ("grok-bot-gc1", thread, turn_id, number, "wake-test-1"),
        )
        row_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    record_wake_result(
        "wake-test-1",
        "pending",
        "composer_not_empty",
        "exact",
        None,
        None,
        wake_detail={
            "release_claim_for_retry": True,
            "generating": False,
            "composer_draft": "x",
            "channel": "dom_paste",
        },
        now="2020-01-02 00:00:00",
    )
    with connect() as conn:
        attempt = conn.execute(
            "SELECT attempt_no, outcome, channel, gate_draft FROM endpoint_wake_attempt "
            "WHERE wake_row_id=?",
            (row_id,),
        ).fetchone()
        assert attempt is not None
        assert int(attempt["attempt_no"]) >= 1
        assert attempt["outcome"] == "pending"
        assert attempt["channel"] == "dom_paste"
        assert int(attempt["gate_draft"]) == 1
