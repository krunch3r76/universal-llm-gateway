"""Retention call-shape, batched delete, and dry-run contracts."""

from __future__ import annotations

import asyncio
import time

import pytest

from event_store.store import EventStore


def _ev(signal: str, role: str, ts: int) -> dict:
    return {
        "signal": signal,
        "role": role,
        "scope": "global",
        "ts_unix_ms": ts,
        "timestamp": "2026-01-01T00:00:00Z",
        "source": "test",
        "payload": {},
    }


@pytest.mark.offline
def test_prune_session_lookup_call_shape_deletes_debug_and_heartbeat() -> None:
    """The session lookup must not pass a third positional argument to query."""

    async def _run() -> tuple[int, int, set[str]]:
        store = EventStore(":memory:")
        await store.open()
        try:
            await store.insert_events(
                [
                    _ev("event.service.started", "coordination", 1_000),
                    _ev("diag.sample", "debug", 100),
                    _ev("federation.telemetry.received", "observation", 100),
                    _ev("keep.me", "observation", 2_000),
                ]
            )
            debug_deleted = await store.prune_debug_events()
            heartbeat_deleted = await store.prune_heartbeat_signals()
            rows = await store.query("SELECT signal FROM events", ())
            return debug_deleted, heartbeat_deleted, {r["signal"] for r in rows}
        finally:
            await store.close()

    debug_deleted, heartbeat_deleted, signals = asyncio.run(_run())
    assert debug_deleted == 1
    assert heartbeat_deleted == 1
    assert "diag.sample" not in signals
    assert "federation.telemetry.received" not in signals
    assert "keep.me" in signals


@pytest.mark.offline
def test_run_retention_batches_and_yields_the_event_loop(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Bounded deletes commit in batches and do not block the event loop."""

    async def _run() -> tuple[int, list[str]]:
        store = EventStore(":memory:")
        store._retention_batch_size = 2
        store._retention_batch_sleep_s = 0.05
        await store.open()
        order: list[str] = []

        async def ticker() -> None:
            await asyncio.sleep(0.02)
            order.append("tick")

        async def run_ret() -> int:
            deleted = await store.run_retention(1_000)
            order.append("ret")
            return deleted

        try:
            now_ms = int(time.time() * 1000)
            old = now_ms - 10_000
            await store.insert_events(
                [_ev(f"old.{i}", "observation", old) for i in range(5)]
                + [_ev("fresh", "observation", now_ms)]
            )
            with caplog.at_level("INFO", logger="event_store.store"):
                deleted, _ticker = await asyncio.gather(run_ret(), ticker())
            rows = await store.query("SELECT signal FROM events", ())
            assert {r["signal"] for r in rows} == {"fresh"}
            return deleted, order
        finally:
            await store.close()

    deleted, order = asyncio.run(_run())
    assert deleted == 5
    assert order == ["tick", "ret"]
    batch_lines = [r.message for r in caplog.records if "Retention batch" in r.message]
    assert len(batch_lines) >= 3
    assert any("WAL checkpoint" in r.message for r in caplog.records)


@pytest.mark.offline
def test_run_retention_dry_run_counts_without_deleting(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Dry-run reports the would-delete count and leaves every row in place."""

    async def _run() -> tuple[int, int]:
        store = EventStore(":memory:")
        await store.open()
        try:
            now_ms = int(time.time() * 1000)
            await store.insert_events(
                [_ev(f"old.{i}", "observation", now_ms - 10_000) for i in range(4)]
                + [_ev("fresh", "observation", now_ms)]
            )
            with caplog.at_level("INFO", logger="event_store.store"):
                would_delete = await store.run_retention(1_000, dry_run=True)
            remaining = await store.query("SELECT COUNT(*) AS n FROM events", ())
            return would_delete, int(remaining[0]["n"])
        finally:
            await store.close()

    would_delete, remaining = asyncio.run(_run())
    assert would_delete == 4
    assert remaining == 5
    assert any("would_delete=4" in r.message for r in caplog.records)
    assert not any("Retention batch" in r.message for r in caplog.records)
