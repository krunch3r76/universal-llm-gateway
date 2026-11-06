"""migration_026 idempotence: wake telemetry columns on endpoint_wake_ledger."""

from __future__ import annotations

import sqlite3

import pytest
from agent_bus_store.db import init_db
from agent_bus_store.db.connection import connect
from agent_bus_store.db.migrations import migration_026


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def test_migration_026_idempotent_columns(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = str(tmp_path / "bus.db")
    monkeypatch.setenv("AGENT_BUS_DB_PATH", db_path)
    init_db()
    with connect() as conn:
        migration_026.run(conn)
        migration_026.run(conn)
        conn.commit()
    with _connect(db_path) as raw:
        cols = {str(r[1]) for r in raw.execute("PRAGMA table_info(endpoint_wake_ledger)")}
    assert "wake_last_attempt_at" in cols
    assert "wake_next_attempt_at" in cols
    assert "wake_attempt_count" in cols
    assert "delivery_failure_detail" in cols
