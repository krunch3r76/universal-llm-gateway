"""NFS pin ordering and reader row-return regression (M10, AC-9)."""

from __future__ import annotations

import asyncio

import pytest

from event_store.store import EventStore


@pytest.mark.asyncio
async def test_nfs_pin_reader_returns_rows(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("EVENTS_SQLITE_NFS_PIN", "1")
    db_path = tmp_path / "events.db"
    store = EventStore(db_path)
    await store.open()
    try:
        await store.insert_events(
            [
                {
                    "signal": "event.service.started",
                    "role": "coordination",
                    "scope": "global",
                    "ts_unix_ms": 1000,
                    "timestamp": "2026-01-01T00:00:00Z",
                    "source": "test",
                    "payload": {},
                }
            ]
        )
        rows = await store.query("SELECT signal FROM events", ())
        assert len(rows) == 1
        assert rows[0]["signal"] == "event.service.started"

        writer_mode = store._db.execute("PRAGMA journal_mode").fetchone()[0]
        lock_mode = store._db.execute("PRAGMA locking_mode").fetchone()[0]
        assert writer_mode.lower() == "wal"
        assert lock_mode.lower() == "exclusive"

        reader = store._reader_connection()
        assert reader is store._db
        mmap = reader.execute("PRAGMA mmap_size").fetchone()[0]
        assert mmap == 0
    finally:
        await store.close()
        monkeypatch.delenv("EVENTS_SQLITE_NFS_PIN", raising=False)


@pytest.mark.asyncio
async def test_open_exclusive_before_wal(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("EVENTS_SQLITE_NFS_PIN", "1")
    db_path = tmp_path / "order.db"
    store = EventStore(db_path)
    await store.open()
    lock_mode = store._db.execute("PRAGMA locking_mode").fetchone()[0]
    journal_mode = store._db.execute("PRAGMA journal_mode").fetchone()[0]
    await store.close()
    assert lock_mode.lower() == "exclusive"
    assert journal_mode.lower() == "wal"
    monkeypatch.delenv("EVENTS_SQLITE_NFS_PIN", raising=False)
