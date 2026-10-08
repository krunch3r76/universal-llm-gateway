"""cortex_conn(read_only=True) skips journal_mode and still reads WAL."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from cortex_store import db as cortex_db

pytestmark = pytest.mark.offline


def _trace_connect(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    logs: list[list[str]] = []
    real_connect = sqlite3.connect

    def tracing_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        conn = real_connect(*args, **kwargs)
        bucket: list[str] = []
        logs.append(bucket)
        conn.set_trace_callback(bucket.append)
        return conn

    monkeypatch.setattr(cortex_db.sqlite3, "connect", tracing_connect)
    return logs


def _wal_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "cortex.db"
    monkeypatch.setattr(cortex_db, "_CORTEX_DB", path)
    writer = cortex_db.cortex_conn()
    writer.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, claim TEXT)")
    writer.execute("INSERT INTO t (id, claim) VALUES (1, 'kept')")
    writer.commit()
    return path


def test_read_only_issues_no_journal_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wal_db(tmp_path, monkeypatch)
    logs = _trace_connect(monkeypatch)
    reader = cortex_db.cortex_conn(read_only=True)
    reader.execute("SELECT 1")
    writer = cortex_db.cortex_conn()
    writer.execute("SELECT 1")
    assert logs
    assert not any("journal_mode" in sql.lower() for sql in logs[0])
    assert any("journal_mode" in sql.lower() for sql in logs[1])
    reader.close()
    writer.close()


def test_read_only_reads_while_writer_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wal_db(tmp_path, monkeypatch)
    writer = cortex_db.cortex_conn()
    reader = cortex_db.cortex_conn(read_only=True)
    row = reader.execute("SELECT claim FROM t WHERE id = 1").fetchone()
    assert row["claim"] == "kept"
    matched = reader.execute(
        "SELECT claim FROM t WHERE claim REGEXP ?", ("ke",)
    ).fetchone()
    assert matched["claim"] == "kept"
    writer.close()
    reader.close()


def test_read_only_opens_wal_db_without_wal_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _wal_db(tmp_path, monkeypatch)
    writer = cortex_db.cortex_conn()
    writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    writer.close()
    wal = Path(str(path) + "-wal")
    shm = Path(str(path) + "-shm")
    wal.unlink(missing_ok=True)
    shm.unlink(missing_ok=True)
    assert not wal.exists()
    assert not shm.exists()
    reader = cortex_db.cortex_conn(read_only=True)
    row = reader.execute("SELECT claim FROM t WHERE id = 1").fetchone()
    assert row["claim"] == "kept"
    reader.close()


def test_read_only_write_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wal_db(tmp_path, monkeypatch)
    reader = cortex_db.cortex_conn(read_only=True)
    with pytest.raises(sqlite3.OperationalError):
        reader.execute("INSERT INTO t (id, claim) VALUES (2, 'nope')")
    reader.close()
