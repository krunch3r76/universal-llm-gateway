"""migration_019 rebuild rolls back a failed copy and skips orphan turn ids."""

from __future__ import annotations

import logging
import os
import sqlite3

import pytest
from agent_bus_store.db import create_thread, init_db, insert_turn
from agent_bus_store.db import migrations as migration_registry


@pytest.fixture(autouse=True)
def _email_bridge_sock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_BRIDGE_SOCK", "/nonexistent")


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
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    db_path, _thread, turn_id, _turn_number = _prepare(tmp_path, monkeypatch)
    real_connect = sqlite3.connect
    _fail_shadow_copy(monkeypatch, db_path)
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        init_db()
    monkeypatch.setattr(sqlite3, "connect", real_connect)
    with caplog.at_level(
        logging.DEBUG, logger="agent_bus_store.db.migrations.migration_019"
    ):
        init_db()
    debugs = [
        rec
        for rec in caplog.records
        if rec.levelno == logging.DEBUG and rec.name.endswith("migration_019")
    ]
    assert any("omitting 0 " in rec.getMessage() for rec in debugs)
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


class _RollbackThenRaise(sqlite3.Connection):
    """Issue a full ROLLBACK, then raise, so savepoint m019 is already gone."""

    def execute(self, sql, parameters=()):  # type: ignore[no-untyped-def]
        if isinstance(sql, str) and "INSERT INTO endpoint_wake_ledger_new" in sql:
            super().execute("ROLLBACK")
            raise sqlite3.OperationalError("disk I/O error")
        return super().execute(sql, parameters)


def _connect_rollback_on_copy(monkeypatch: pytest.MonkeyPatch, db_path: str) -> None:
    real_connect = sqlite3.connect

    def connect(database, *args, **kwargs):  # type: ignore[no-untyped-def]
        if os.path.abspath(str(database)) == os.path.abspath(db_path):
            kwargs["factory"] = _RollbackThenRaise
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)


def test_autorollback_propagates_original_error(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path, _thread, _turn_id, _turn_number = _prepare(tmp_path, monkeypatch)
    _connect_rollback_on_copy(monkeypatch, db_path)
    with pytest.raises(sqlite3.OperationalError, match="disk I/O error") as raised:
        init_db()
    assert "no such savepoint" not in str(raised.value)


class _DdlFreeEarlier:
    """Unrecorded predecessor. No DDL: the runner's schema_migrations INSERT opens the txn."""

    MIGRATION_ID = "migration_test_before_019"

    @staticmethod
    def run(conn: sqlite3.Connection) -> None:
        conn.execute("SELECT 1")


def _insert_earlier_migration(monkeypatch: pytest.MonkeyPatch) -> None:
    earlier = _DdlFreeEarlier()
    patched = []
    for migration in migration_registry.MIGRATIONS:
        if migration.MIGRATION_ID == "migration_019":
            patched.append(earlier)
        patched.append(migration)
    monkeypatch.setattr(migration_registry, "MIGRATIONS", patched)


def test_failed_copy_inside_open_transaction_retries_clean(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path, _thread, turn_id, _turn_number = _prepare(tmp_path, monkeypatch)
    _insert_earlier_migration(monkeypatch)
    real_connect = sqlite3.connect
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
        recorded = {
            row["id"] for row in conn.execute("SELECT id FROM schema_migrations")
        }
        assert "migration_019" not in recorded
        assert _DdlFreeEarlier.MIGRATION_ID not in recorded
    monkeypatch.setattr(sqlite3, "connect", real_connect)
    init_db()
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT turn_id, route FROM endpoint_wake_ledger"
        ).fetchall()
        assert [(row["turn_id"], row["route"]) for row in rows] == [(turn_id, "exact")]
        assert "parent" in _ledger_sql(conn)
        recorded = {
            row["id"] for row in conn.execute("SELECT id FROM schema_migrations")
        }
        assert "migration_019" in recorded
        assert _DdlFreeEarlier.MIGRATION_ID in recorded


def test_orphan_turn_id_is_skipped_and_parent_route_is_accepted(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
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
    with caplog.at_level(
        logging.DEBUG, logger="agent_bus_store.db.migrations.migration_019"
    ):
        init_db()
    warnings = [
        rec
        for rec in caplog.records
        if rec.levelno == logging.WARNING and rec.name.endswith("migration_019")
    ]
    assert len(warnings) == 1
    assert "omitting 1 " in warnings[0].getMessage()
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
