"""migration_019 rebuild rolls back a failed copy and skips orphan turn ids."""

from __future__ import annotations

import os
import sqlite3

import pytest

os.environ["EMAIL_BRIDGE_SOCK"] = "/nonexistent"

from agent_bus_store.db import create_thread, init_db, insert_turn  # noqa: E402

pytestmark = pytest.mark.offline

_OLD_LEDGER = """\
CREATE TABLE endpoint_wake_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    turn_id INTEGER NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
    turn_number INTEGER NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN ('pending','claimed','delivered','refused','skipped','unknown')
    ),
    wake_id TEXT,
    route TEXT CHECK (route IN ('exact','wildcard','cse')),
    subscription_id INTEGER,
    cse_registration_id TEXT,
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    claimed_at TEXT,
    finished_at TEXT,
    UNIQUE(agent, thread_id, turn_id)
)
"""

_ORPHAN_TURN_ID = 2_000_000_000


def _prepare(tmp_path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, str, int, int]:
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    monkeypatch.setenv("EMAIL_BRIDGE_SOCK", "/nonexistent")
    init_db()
    detail = create_thread(thread_id=None, slug="pre-019")
    assert detail is not None
    thread = str(detail["id"])
    turn_id, _ts, turn_number, _claimed = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="FACT|pre-019",
        body="b",
    )
    raw = sqlite3.connect(db_path)
    raw.execute("PRAGMA foreign_keys=OFF")
    raw.execute("DROP TABLE endpoint_wake_ledger")
    raw.execute(_OLD_LEDGER)
    raw.execute("DELETE FROM schema_migrations WHERE id = 'migration_019'")
    raw.execute(
        "INSERT INTO endpoint_wake_ledger "
        "(agent, thread_id, turn_id, turn_number, state, route) "
        "VALUES (?, ?, ?, ?, 'pending', 'exact')",
        ("grok-bot-gc1", thread, turn_id, turn_number),
    )
    raw.commit()
    raw.close()
    return str(db_path), thread, turn_id, turn_number


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _ledger_sql(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'endpoint_wake_ledger'"
    ).fetchone()
    assert row is not None
    return str(row["sql"])


def _fail_shadow_copy(monkeypatch: pytest.MonkeyPatch, db_path: str) -> None:
    """Deny the copy INSERT on the init_db connection. Connection.execute is immutable."""
    real_connect = sqlite3.connect

    def connect(database, *args, **kwargs):  # type: ignore[no-untyped-def]
        conn = real_connect(database, *args, **kwargs)
        if os.path.abspath(str(database)) == os.path.abspath(db_path):

            def authorizer(action, arg1, arg2, db_name, source):  # type: ignore[no-untyped-def]
                if (
                    action == sqlite3.SQLITE_INSERT
                    and arg1 == "endpoint_wake_ledger_new"
                ):
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            conn.set_authorizer(authorizer)
        return conn

    monkeypatch.setattr(sqlite3, "connect", connect)


def test_pre_019_seed_has_real_turns(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path, thread, turn_id, _turn_number = _prepare(tmp_path, monkeypatch)
    with _connect(db_path) as conn:
        turn = conn.execute(
            "SELECT id, thread FROM turns WHERE id = ?", (turn_id,)
        ).fetchone()
        assert turn is not None
        assert turn["thread"] == thread
        ledger = conn.execute(
            "SELECT turn_id, route FROM endpoint_wake_ledger"
        ).fetchall()
        assert [(row["turn_id"], row["route"]) for row in ledger] == [
            (turn_id, "exact")
        ]
        assert "parent" not in _ledger_sql(conn)
        recorded = conn.execute(
            "SELECT id FROM schema_migrations WHERE id = 'migration_019'"
        ).fetchone()
        assert recorded is None


def test_failed_copy_leaves_no_shadow_and_keeps_rows(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path, _thread, turn_id, _turn_number = _prepare(tmp_path, monkeypatch)
    _fail_shadow_copy(monkeypatch, db_path)
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        init_db()
    with _connect(db_path) as conn:
        names = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "endpoint_wake_ledger_new" not in names
        rows = conn.execute(
            "SELECT turn_id, route, state FROM endpoint_wake_ledger"
        ).fetchall()
        assert [(row["turn_id"], row["route"], row["state"]) for row in rows] == [
            (turn_id, "exact", "pending")
        ]
        assert "parent" not in _ledger_sql(conn)
        recorded = conn.execute(
            "SELECT id FROM schema_migrations WHERE id = 'migration_019'"
        ).fetchone()
        assert recorded is None


def test_init_db_after_failed_copy_records_migration(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path, _thread, turn_id, _turn_number = _prepare(tmp_path, monkeypatch)
    real_connect = sqlite3.connect
    _fail_shadow_copy(monkeypatch, db_path)
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        init_db()
    monkeypatch.setattr(sqlite3, "connect", real_connect)
    init_db()
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT turn_id, route, state FROM endpoint_wake_ledger"
        ).fetchall()
        assert [(row["turn_id"], row["route"], row["state"]) for row in rows] == [
            (turn_id, "exact", "pending")
        ]
        assert "parent" in _ledger_sql(conn)
        recorded = conn.execute(
            "SELECT id FROM schema_migrations WHERE id = 'migration_019'"
        ).fetchone()
        assert recorded is not None


def test_precreated_shadow_table_is_replaced(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path, _thread, turn_id, _turn_number = _prepare(tmp_path, monkeypatch)
    with _connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE endpoint_wake_ledger_new (id INTEGER PRIMARY KEY, note TEXT)"
        )
        conn.execute("INSERT INTO endpoint_wake_ledger_new (note) VALUES ('stale')")
        conn.commit()
    init_db()
    with _connect(db_path) as conn:
        names = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "endpoint_wake_ledger_new" not in names
        rows = conn.execute(
            "SELECT turn_id, route FROM endpoint_wake_ledger"
        ).fetchall()
        assert [(row["turn_id"], row["route"]) for row in rows] == [(turn_id, "exact")]
        assert "parent" in _ledger_sql(conn)
        recorded = conn.execute(
            "SELECT id FROM schema_migrations WHERE id = 'migration_019'"
        ).fetchone()
        assert recorded is not None


def test_orphan_turn_id_is_skipped_and_parent_route_is_accepted(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path, thread, turn_id, turn_number = _prepare(tmp_path, monkeypatch)
    with _connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            "INSERT INTO endpoint_wake_ledger "
            "(agent, thread_id, turn_id, turn_number, state, route) "
            "VALUES (?, ?, ?, ?, 'pending', 'wildcard')",
            ("orphan-agent", thread, _ORPHAN_TURN_ID, 99),
        )
        conn.commit()
        present = conn.execute(
            "SELECT id FROM turns WHERE id = ?", (_ORPHAN_TURN_ID,)
        ).fetchone()
        assert present is None
    init_db()
    with _connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        rows = conn.execute(
            "SELECT agent, turn_id FROM endpoint_wake_ledger ORDER BY turn_id"
        ).fetchall()
        assert [(row["agent"], row["turn_id"]) for row in rows] == [
            ("grok-bot-gc1", turn_id)
        ]
        recorded = conn.execute(
            "SELECT id FROM schema_migrations WHERE id = 'migration_019'"
        ).fetchone()
        assert recorded is not None
        conn.execute(
            "INSERT INTO endpoint_wake_ledger "
            "(agent, thread_id, turn_id, turn_number, state, route) "
            "VALUES (?, ?, ?, ?, 'pending', 'parent')",
            ("other-agent", thread, turn_id, turn_number),
        )
        conn.commit()
        parent = conn.execute(
            "SELECT route FROM endpoint_wake_ledger WHERE agent = 'other-agent'"
        ).fetchone()
        assert parent is not None
        assert parent["route"] == "parent"
