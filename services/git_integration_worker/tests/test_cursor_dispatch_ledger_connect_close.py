"""Ledger _connect closes connections (cpu-pin H3)."""

from __future__ import annotations

import sqlite3

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    _connect,
    _open_ledger_connection,
)


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


def test_module_connect_closes_and_commits(tmp_path) -> None:
    db = tmp_path / "close.db"
    held: list[sqlite3.Connection] = []
    with _connect(db) as conn:
        held.append(conn)
        conn.execute("CREATE TABLE t(x INTEGER)")
        conn.execute("INSERT INTO t(x) VALUES (1)")
    with pytest.raises(sqlite3.ProgrammingError):
        held[0].execute("SELECT 1")
    verify = _open_ledger_connection(db)
    try:
        row = verify.execute("SELECT x FROM t").fetchone()
        assert row["x"] == 1
    finally:
        verify.close()


def test_module_connect_rolls_back_on_error(tmp_path) -> None:
    db = tmp_path / "rollback.db"
    with _connect(db) as conn:
        conn.execute("CREATE TABLE t(x INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO t(x) VALUES (1)")
    with pytest.raises(RuntimeError):
        with _connect(db) as conn:
            conn.execute("INSERT INTO t(x) VALUES (2)")
            raise RuntimeError("boom")
    verify = _open_ledger_connection(db)
    try:
        count = verify.execute("SELECT COUNT(*) AS n FROM t").fetchone()["n"]
    finally:
        verify.close()
    assert count == 1


def test_ledger_instance_connect_closes(tmp_path) -> None:
    ledger = CursorDispatchLedger.instance()
    held: list[sqlite3.Connection] = []
    with ledger._connect() as conn:
        held.append(conn)
        conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' LIMIT 1"
        )
    with pytest.raises(sqlite3.ProgrammingError):
        held[0].execute("SELECT 1")
