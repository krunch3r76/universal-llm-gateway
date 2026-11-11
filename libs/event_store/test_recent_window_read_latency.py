"""30-minute recent-window reads by model_id and by signal stay bounded.

The bound is the query plan plus SQLite VM work (progress-handler ticks), not
wall-clock time, so it does not depend on host speed. A plan that falls back to
a temp b-tree sort or a table scan visits every window row and blows the bound.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from typing import Any

import pytest

from event_store.schema import build_model_id_ts_index
from event_store.store import EventStore

_MODEL_ID = "openrouter/anthropic/claude-sonnet-4-5"
_SIGNAL = "mcp.transport.done"
_WINDOW_MS = 30 * 60 * 1000
_WINDOW_ROWS = 4000
_LIMIT = 50
_OPS_PER_TICK = 100
_MAX_TICKS = 100


def _ev(*, signal: str, model_id: str, ts: int) -> dict[str, Any]:
    return {
        "signal": signal,
        "role": "observation",
        "scope": "global",
        "ts_unix_ms": ts,
        "timestamp": "2026-11-08T00:00:00Z",
        "source": "latency-test",
        "payload": {"model_id": model_id},
    }


def _plan(db: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> str:
    rows = db.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
    return " | ".join(str(row[-1]) for row in rows)


def _ticks(db: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> int:
    count = 0

    def _tick() -> int:
        nonlocal count
        count += 1
        return 0

    db.set_progress_handler(_tick, _OPS_PER_TICK)
    try:
        db.execute(sql, params).fetchall()
    finally:
        db.set_progress_handler(None, 0)
    return count


@pytest.mark.offline
def test_thirty_minute_model_id_and_signal_reads_are_index_bounded() -> None:
    async def _run() -> dict[str, Any]:
        store = EventStore(":memory:")
        await store.open()
        try:
            now_ms = int(time.time() * 1000)
            window_start = now_ms - _WINDOW_MS
            old = now_ms - 7 * 24 * 60 * 60 * 1000
            batch = [
                _ev(signal=_SIGNAL, model_id=_MODEL_ID, ts=window_start + i * 400)
                for i in range(_WINDOW_ROWS)
            ] + [
                _ev(signal="other.noise", model_id="other/model", ts=old + i)
                for i in range(500)
            ]
            await store.insert_events(batch)
            db = store._db
            assert db is not None
            assert build_model_id_ts_index(db) is True

            model_sql = (
                "SELECT seq FROM events WHERE model_id = ? AND ts_unix_ms >= ? "
                "ORDER BY ts_unix_ms DESC LIMIT ?"
            )
            model_params = (_MODEL_ID, window_start, _LIMIT)
            signal_sql = (
                "SELECT seq FROM events WHERE signal = ? AND ts_unix_ms >= ? "
                "ORDER BY ts_unix_ms DESC LIMIT ?"
            )
            signal_params = (_SIGNAL, window_start, _LIMIT)
            full_sql = (
                "SELECT seq FROM events NOT INDEXED WHERE model_id = ? "
                "AND ts_unix_ms >= ? ORDER BY ts_unix_ms DESC LIMIT ?"
            )
            return {
                "model_plan": _plan(db, model_sql, model_params),
                "signal_plan": _plan(db, signal_sql, signal_params),
                "model_ticks": _ticks(db, model_sql, model_params),
                "signal_ticks": _ticks(db, signal_sql, signal_params),
                "full_ticks": _ticks(db, full_sql, model_params),
            }
        finally:
            await store.close()

    r = asyncio.run(_run())
    assert "idx_model_id_ts" in r["model_plan"], r["model_plan"]
    assert "TEMP B-TREE" not in r["model_plan"], r["model_plan"]
    assert "idx_signal_ts" in r["signal_plan"], r["signal_plan"]
    assert "TEMP B-TREE" not in r["signal_plan"], r["signal_plan"]
    assert r["model_ticks"] <= _MAX_TICKS, r
    assert r["signal_ticks"] <= _MAX_TICKS, r
    assert r["model_ticks"] * 10 < r["full_ticks"], r
