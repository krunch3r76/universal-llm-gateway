"""30-minute recent-window reads must stay under 5 ms (model_id and signal prefix)."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from event_store.operation_parameters import signal_prefix_range
from event_store.store import EventStore

_MODEL_ID = "openrouter/anthropic/claude-sonnet-4-5"
_WINDOW_MS = 30 * 60 * 1000
_DEADLINE_S = 0.005
_ROW_COUNT = 4000


def _ev(
    *,
    signal: str,
    model_id: str | None,
    ts: int,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if model_id is not None:
        payload["model_id"] = model_id
    return {
        "signal": signal,
        "role": "observation",
        "scope": "global",
        "ts_unix_ms": ts,
        "timestamp": "2026-11-08T00:00:00Z",
        "source": "latency-test",
        "payload": payload,
    }


def _plan_uses_model_ts_index(store: EventStore, sql: str, params: tuple[Any, ...]) -> bool:
    assert store._db is not None
    rows = store._db.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
    detail = " | ".join(str(row[-1]) for row in rows)
    return "idx_model_id_ts" in detail


@pytest.mark.offline
def test_thirty_minute_model_id_and_signal_reads_under_five_ms() -> None:
    async def _run() -> tuple[float, float]:
        store = EventStore(":memory:")
        await store.open()
        try:
            now_ms = int(time.time() * 1000)
            window_start = now_ms - _WINDOW_MS
            noise_start = now_ms - (7 * 24 * 60 * 60 * 1000)
            batch: list[dict[str, Any]] = []
            for i in range(_ROW_COUNT):
                ts = window_start + (i % _WINDOW_MS)
                batch.append(_ev(signal="mcp.transport.done", model_id=_MODEL_ID, ts=ts))
            for i in range(500):
                batch.append(
                    _ev(
                        signal="other.noise",
                        model_id="other/model",
                        ts=noise_start + i,
                    )
                )
            await store.insert_events(batch)

            model_sql = (
                "SELECT seq FROM events WHERE model_id = ? AND ts_unix_ms >= ? "
                "ORDER BY ts_unix_ms DESC LIMIT 50"
            )
            model_params = (_MODEL_ID, window_start)
            assert _plan_uses_model_ts_index(store, model_sql, model_params)

            def _sync_timed(sql: str, params: tuple[Any, ...]) -> float:
                conn = store._reader_connection()
                conn.execute(sql, params).fetchall()
                samples: list[float] = []
                for _ in range(5):
                    start = time.perf_counter()
                    conn.execute(sql, params).fetchall()
                    samples.append(time.perf_counter() - start)
                return min(samples)

            model_elapsed = _sync_timed(model_sql, model_params)

            lower, upper = signal_prefix_range("mcp.transport.")
            signal_sql = (
                "SELECT seq, signal, source, timestamp, execution_id, model_id, payload "
                "FROM events WHERE signal >= ? AND signal < ? AND ts_unix_ms >= ? "
                "ORDER BY ts_unix_ms DESC LIMIT ?"
            )
            signal_params = (lower, upper, window_start, 50)
            assert store._db is not None
            signal_plan = " | ".join(
                str(row[-1])
                for row in store._db.execute(
                    "EXPLAIN QUERY PLAN " + signal_sql, signal_params
                ).fetchall()
            )
            assert "idx_signal_ts" in signal_plan
            signal_elapsed = _sync_timed(signal_sql, signal_params)
            return model_elapsed, signal_elapsed
        finally:
            await store.close()

    model_elapsed, signal_elapsed = asyncio.run(_run())
    assert model_elapsed < _DEADLINE_S, f"model_id read took {model_elapsed * 1000:.2f} ms"
    assert signal_elapsed < _DEADLINE_S, (
        f"signal window read took {signal_elapsed * 1000:.2f} ms"
    )
