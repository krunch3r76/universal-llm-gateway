"""Maintenance CLI dry-run safety and execute path."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from event_store.maintenance import run_maintenance
from event_store.schema import _SCHEMA_SQL


def _make_db(path: Path, *, rows: int = 5) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA_SQL)
    now = int(time.time() * 1000)
    for i in range(rows):
        conn.execute(
            "INSERT INTO events (signal, role, scope, ts_unix_ms, timestamp, source, payload) "
            "VALUES (?, 'observation', 'global', ?, 't', 'test', '{}')",
            ("test.signal", now - i * 86400 * 1000),
        )
    conn.commit()
    conn.close()


@pytest.mark.offline
def test_dry_run_does_not_delete(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    _make_db(db, rows=10)
    run_maintenance(["--db", str(db)])
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.close()
    assert n == 10


@pytest.mark.offline
def test_refuses_live_path_without_flag(tmp_path: Path) -> None:
    live = Path("/home/io/.events/events.db")
    if not live.is_file():
        pytest.skip("live events.db not present")
    with pytest.raises(SystemExit):
        run_maintenance(["--db", str(live)])


@pytest.mark.offline
def test_refuses_when_write_lock_held(tmp_path: Path) -> None:
    db = tmp_path / "busy.db"
    _make_db(db)
    holder = sqlite3.connect(db, isolation_level=None)
    holder.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(SystemExit, match="busy"):
            run_maintenance(["--db", str(db)])
    finally:
        holder.execute("ROLLBACK")
        holder.close()


@pytest.mark.offline
def test_execute_deletes_and_builds_index(tmp_path: Path) -> None:
    db = tmp_path / "exec.db"
    _make_db(db, rows=20)
    run_maintenance(["--db", str(db), "--execute", "--days", "3"])
    conn = sqlite3.connect(db)
    remaining = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    idx = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='idx_model_id_ts'"
    ).fetchone()
    conn.close()
    assert remaining < 20
    assert idx is not None
