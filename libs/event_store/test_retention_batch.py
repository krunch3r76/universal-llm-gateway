"""Retention call-shape, batched delete, and dry-run contracts."""

from __future__ import annotations

import asyncio
import logging
import time

import pytest

from event_store.errors import EventStoreReadDeadlineError
from event_store.server import _retention_loop, run_retention_pass
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
    assert any("elapsed_ms=" in line for line in batch_lines)


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


@pytest.mark.offline
def test_session_keyset_progress_stays_flat_as_coordination_is_retained() -> None:
    """Each session batch walks a flat opcode count, not the retained prefix."""

    async def _run() -> list[int]:
        store = EventStore(":memory:")
        store._retention_batch_size = 25
        store._retention_batch_sleep_s = 0
        await store.open()
        counts: list[int] = []
        bucket = {"n": 0}

        def progress() -> int:
            bucket["n"] += 1
            return 0

        assert store._db is not None
        store._db.set_progress_handler(progress, 100)

        class _Snap(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                if record.getMessage().startswith("Retention batch table=events"):
                    counts.append(bucket["n"])
                    bucket["n"] = 0

        handler = _Snap()
        logging.getLogger("event_store.store").addHandler(handler)
        try:
            rows = []
            for i in range(400):
                rows.append(_ev(f"c.{i}", "coordination", 1_000))
                rows.append(_ev(f"o.{i}", "observation", 1_000))
            rows.append(_ev("event.service.started", "coordination", 5_000))
            await store.insert_events(rows)
            await store.run_session_retention(1)
            return counts
        finally:
            logging.getLogger("event_store.store").removeHandler(handler)
            await store.close()

    counts = asyncio.run(_run())
    assert len(counts) >= 10
    assert counts[-1] <= max(counts[0] * 3, counts[0] + 40)


@pytest.mark.offline
def test_age_pass_runs_before_session_pass() -> None:
    """The age delete runs first so the session pass does not rescan it."""

    async def _run() -> list[str]:
        store = EventStore(":memory:")
        await store.open()
        order: list[str] = []
        age = store.run_retention
        session = store.run_session_retention

        async def run_age(*args: object, **kwargs: object) -> int:
            order.append("age")
            return await age(*args, **kwargs)  # type: ignore[arg-type]

        async def run_session(*args: object, **kwargs: object) -> int:
            order.append("session")
            return await session(*args, **kwargs)  # type: ignore[arg-type]

        store.run_retention = run_age  # type: ignore[method-assign]
        store.run_session_retention = run_session  # type: ignore[method-assign]
        try:
            await run_retention_pass(
                store, retention_days=7, max_sessions=2, dry_run=True
            )
            return order
        finally:
            await store.close()

    order = asyncio.run(_run())
    assert order.index("age") < order.index("session")


@pytest.mark.offline
def test_boundary_deadline_returns_zero_and_loop_continues(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A deadline on the boundary lookup does not end the retention loop."""

    async def _run() -> tuple[int, bool, list[str]]:
        store = EventStore(":memory:")
        await store.open()
        seen: list[str] = []

        async def raising(*_args: object, **_kwargs: object) -> list[dict]:
            raise EventStoreReadDeadlineError("deadline")

        async def boom(**_kwargs: object) -> int:
            seen.append("debug")
            raise EventStoreReadDeadlineError("step")

        async def age(*_args: object, **_kwargs: object) -> int:
            seen.append("age")
            return 0

        store.query = raising  # type: ignore[method-assign]
        try:
            with caplog.at_level("ERROR", logger="event_store.store"):
                missed = await store.prune_heartbeat_signals()
            store.prune_debug_events = boom  # type: ignore[method-assign]
            store.run_retention = age  # type: ignore[method-assign]
            task = asyncio.create_task(
                _retention_loop(store, retention_days=7, max_sessions=2)
            )
            await asyncio.sleep(0.2)
            alive = not task.done()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            return missed, alive, seen
        finally:
            await store.close()

    missed, alive, seen = asyncio.run(_run())
    assert missed == 0
    assert alive
    assert seen[:2] == ["debug", "age"]
    assert any("boundary lookup failed" in r.message for r in caplog.records)


@pytest.mark.offline
def test_startup_dry_run_logs_would_delete_and_keeps_rows(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The default startup gate counts rows and does not delete them."""
    monkeypatch.setenv("EVENTS_RETENTION_STARTUP", "dry_run")

    async def _run() -> int:
        store = EventStore(":memory:")
        store._retention_batch_sleep_s = 0
        await store.open()
        try:
            now_ms = int(time.time() * 1000)
            await store.insert_events(
                [_ev("old", "observation", now_ms - 10 * 86_400_000)]
            )
            with caplog.at_level("INFO"):
                task = asyncio.create_task(
                    _retention_loop(store, retention_days=1, max_sessions=2)
                )
                await asyncio.sleep(0.3)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            rows = await store.query("SELECT COUNT(*) AS n FROM events", ())
            return int(rows[0]["n"])
        finally:
            await store.close()

    remaining = asyncio.run(_run())
    assert remaining == 1
    assert any("would_delete=" in r.message for r in caplog.records)
    assert any("dry_run=True" in r.message for r in caplog.records)
