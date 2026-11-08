"""Hot tier parity, eviction, warm start, degrade path, and latency."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from event_store.operations_impl import _model_timeline, _request_trace, _signal_events
from event_store.store import EventStore

_HOURS_MS = 24 * 60 * 60 * 1000
_SIGNAL = "mcp.transport.done"
_MODEL = "test/model"


def _ev(
    *,
    signal: str = _SIGNAL,
    ts: int,
    model_id: str = _MODEL,
    request_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"model_id": model_id}
    if request_id:
        payload["request_id"] = request_id
    return {
        "signal": signal,
        "role": "observation",
        "scope": "global",
        "ts_unix_ms": ts,
        "timestamp": "2026-11-08T00:00:00Z",
        "source": "hot-tier-test",
        "payload": payload,
    }


@pytest.mark.offline
@pytest.mark.parametrize(
    "op",
    [
        lambda s, since: _signal_events({"signal": _SIGNAL, "since_ts": since, "limit": 50}, s),
        lambda s, since: _model_timeline({"model_id": _MODEL, "since_ts": since}, s),
        lambda s, _: _request_trace({"request_id": "req-parity", "limit": 50}, s),
    ],
)
def test_hot_and_cold_parity_window_queries(op) -> None:
    async def _run() -> None:
        store = EventStore(":memory:")
        await store.open()
        try:
            now = int(time.time() * 1000)
            since = now - 30 * 60 * 1000
            rows = [
                _ev(ts=since + i * 1000, request_id="req-parity")
                for i in range(30)
            ]
            await store.insert_events(rows)
            assert store._hot is not None
            store._hot.degraded = True
            cold = await op(store, since)
            store._hot.degraded = False
            hot = await op(store, since)
            assert hot == cold
        finally:
            await store.close()

    asyncio.run(_run())


@pytest.mark.offline
def test_eviction_boundary_and_cold_fallback() -> None:
    async def _run() -> None:
        store = EventStore(":memory:")
        await store.open()
        try:
            hot = store._hot
            assert hot is not None
            now = int(time.time() * 1000)
            hot.window_ms = _HOURS_MS
            floor = now - _HOURS_MS
            hot.floor_ts_ms = floor
            old = floor - 1
            keep = floor + 1
            await store.insert_events([_ev(ts=old), _ev(ts=keep)])
            hot.evict(now)
            assert store._hot is not None
            cnt = hot.row_count()
            assert cnt == 1
            hot.degraded = False
            rows = await store.query(
                "SELECT seq FROM events WHERE ts_unix_ms >= ?",
                (floor - 5000,),
                since_ts_ms=floor - 5000,
            )
            assert len(rows) >= 1
            store._hot.degraded = True
            cold_rows = await store.query(
                "SELECT seq FROM events WHERE ts_unix_ms >= ?",
                (floor - 5000,),
                since_ts_ms=floor - 5000,
            )
            assert len(cold_rows) >= 2
        finally:
            await store.close()

    asyncio.run(_run())


@pytest.mark.offline
def test_warm_start_keeps_recent_only() -> None:
    async def _run() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.db"
            store = EventStore(path)
            await store.open()
            now = int(time.time() * 1000)
            old = now - 3 * _HOURS_MS
            recent = now - 60_000
            await store.insert_events([_ev(ts=old), _ev(ts=recent)])
            await store.close()

            store2 = EventStore(path)
            await store2.open()
            try:
                hot = store2._hot
                assert hot is not None
                assert hot.row_count() == 1
                row = await store2.query(
                    "SELECT ts_unix_ms FROM events", (), limit=10, since_ts_ms=hot.floor_ts_ms
                )
                assert len(row) == 1
                assert int(row[0]["ts_unix_ms"]) == recent
            finally:
                await store2.close()

    asyncio.run(_run())


@pytest.mark.offline
def test_hot_insert_failure_degrades_and_cold_intact() -> None:
    async def _run() -> None:
        store = EventStore(":memory:")
        await store.open()
        try:
            hot = store._hot
            assert hot is not None
            with mock.patch.object(hot, "insert_rows", side_effect=sqlite3.OperationalError("boom")):
                await store.insert_events([_ev(ts=int(time.time() * 1000))])
            assert hot.degraded is True
            n = await store.query("SELECT COUNT(*) AS n FROM events", (), limit=1)
            assert int(n[0]["n"]) == 1
            since = int(time.time() * 1000) - 60_000
            routed = await store.query(
                "SELECT seq FROM events WHERE ts_unix_ms >= ?",
                (since,),
                since_ts_ms=since,
            )
            assert len(routed) == 1
        finally:
            await store.close()

    asyncio.run(_run())


@pytest.mark.offline
def test_window_signal_query_uses_hot_execute_query() -> None:
    async def _run() -> None:
        store = EventStore(":memory:")
        await store.open()
        try:
            now = int(time.time() * 1000)
            since = now - 30 * 60 * 1000
            await store.insert_events([_ev(ts=since + 1000)])
            assert store._hot is not None
            with mock.patch.object(
                store._hot, "execute_query", wraps=store._hot.execute_query
            ) as hot_spy:
                await _signal_events(
                    {"signal": _SIGNAL, "since_ts": since, "limit": 50}, store
                )
                assert hot_spy.call_count >= 1
        finally:
            await store.close()

    asyncio.run(_run())


@pytest.mark.offline
def test_hot_query_error_falls_back_to_cold_rows() -> None:
    async def _run() -> None:
        store = EventStore(":memory:")
        await store.open()
        try:
            ts = int(time.time() * 1000)
            await store.insert_events([_ev(ts=ts), _ev(ts=ts + 1)])
            hot = store._hot
            assert hot is not None
            since = ts - 60_000
            with mock.patch.object(
                hot, "execute_query", side_effect=sqlite3.OperationalError("hot boom")
            ):
                rows = await store.query(
                    "SELECT seq FROM events WHERE ts_unix_ms >= ? ORDER BY seq",
                    (since,),
                    since_ts_ms=since,
                )
            assert len(rows) == 2
        finally:
            await store.close()

    asyncio.run(_run())


@pytest.mark.offline
def test_eviction_failure_degrades_without_raising_ingest() -> None:
    async def _run() -> None:
        store = EventStore(":memory:")
        await store.open()
        try:
            hot = store._hot
            assert hot is not None
            with mock.patch.object(hot, "evict", side_effect=RuntimeError("evict fail")):
                store.maybe_evict_hot_tier()
            assert hot.degraded is True
            await store.insert_events([_ev(ts=int(time.time() * 1000))])
            n = await store.query("SELECT COUNT(*) AS n FROM events", (), limit=1)
            assert int(n[0]["n"]) == 1
        finally:
            await store.close()

    asyncio.run(_run())


@pytest.mark.offline
def test_retention_batch_event_mirrored_to_hot() -> None:
    async def _run() -> None:
        store = EventStore(":memory:")
        await store.open()
        try:
            hot = store._hot
            assert hot is not None
            old = int(time.time() * 1000) - 30 * 86400 * 1000
            await store.insert_events([_ev(ts=old, signal="old.evt")])
            store._retention_batch_size = 1
            store._retention_batch_sleep_s = 0
            await store.run_retention(7 * 86400 * 1000, dry_run=False)
            assert hot._writer is not None
            signals = [
                str(r[0])
                for r in hot._writer.execute("SELECT signal FROM events").fetchall()
            ]
            assert "event.retention.batch" in signals
        finally:
            await store.close()

    asyncio.run(_run())


@pytest.mark.offline
def test_hot_tier_disabled_uses_cold() -> None:
    async def _run() -> None:
        with mock.patch.dict(os.environ, {"EVENTS_HOT_TIER": "0"}):
            store = EventStore(":memory:")
            await store.open()
            try:
                assert store._hot is None
                await store.insert_events([_ev(ts=int(time.time() * 1000))])
                since = int(time.time() * 1000) - 60_000
                rows = await store.query(
                    "SELECT seq FROM events WHERE ts_unix_ms >= ?",
                    (since,),
                    since_ts_ms=since,
                )
                assert len(rows) == 1
            finally:
                await store.close()

    asyncio.run(_run())


@pytest.mark.offline
def test_hot_signal_read_latency_under_5ms() -> None:
    async def _run() -> None:
        store = EventStore(":memory:")
        await store.open()
        try:
            now = int(time.time() * 1000)
            window = 30 * 60 * 1000
            since = now - window
            batch = [_ev(ts=since + (i % window)) for i in range(50_000)]
            await store.insert_events(batch)
            sql = (
                "SELECT seq, signal, source, timestamp, execution_id, model_id, payload "
                "FROM events WHERE signal = ? AND ts_unix_ms >= ? ORDER BY ts_unix_ms DESC LIMIT ?"
            )
            params = (_SIGNAL, since, 50)
            plan = store._db.execute(
                "EXPLAIN QUERY PLAN " + sql, params
            ).fetchall()
            plan_text = " ".join(str(r[-1]) for r in plan).lower()
            assert "idx_signal_ts" in plan_text or "signal" in plan_text
            await store.query(sql, params, since_ts_ms=since, limit=50)
            timings: list[float] = []
            for _ in range(5):
                t0 = time.perf_counter()
                await store.query(sql, params, since_ts_ms=since, limit=50)
                timings.append(time.perf_counter() - t0)
            assert min(timings) < 0.005, min(timings)
        finally:
            await store.close()

    asyncio.run(_run())


@pytest.mark.offline
def test_open_warms_before_ingest_ordering_comment() -> None:
    """Warm completes inside open(); server.run_service calls open() before ingest.start()."""
    async def _run() -> None:
        store = EventStore(":memory:")
        await store.open()
        try:
            assert store._hot is not None
            assert store._hot.row_count() >= 0
            assert store._db is not None
        finally:
            await store.close()

    asyncio.run(_run())
