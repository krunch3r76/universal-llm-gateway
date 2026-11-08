"""Background ide_context_hop_monitor loop — decoupled from wake passes."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest


def _stub_dispatcher_noops(monkeypatch: pytest.MonkeyPatch) -> None:
    def _noop(*_a: object, **_k: object) -> None:
        return None

    monkeypatch.setattr(
        "bus_watch.endpoint_wake_composer_defer.normalize_legacy_pending_wakes",
        _noop,
    )
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_composer_defer.escalate_composer_deferred_on_sweep",
        _noop,
    )
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.expire_overdue_hops",
        _noop,
    )
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.scan_once",
        _noop,
    )
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.fire_due_wakes",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_composer_defer.finalize_composer_defer_pass",
        _noop,
    )
    monkeypatch.setattr(
        "bus_watch.impresario_ear_patrol.run_patrol_pass",
        _noop,
    )


def test_wake_pass_does_not_await_slow_hop_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dispatcher passes stay fast while the hop monitor runs a slow check."""
    from bus_watch.endpoint_wake_dispatcher import run_dispatcher
    from bus_watch.ide_context_hop_monitor import run_ide_context_hop_monitor

    _stub_dispatcher_noops(monkeypatch)

    def _slow_check(*_a: object, **_k: object) -> None:
        time.sleep(5)

    monkeypatch.setattr(
        "bus_watch.ide_context_hop_monitor.check_cursor_ide_context_hops",
        _slow_check,
    )
    monkeypatch.setattr(
        "bus_watch.ide_context_hop_monitor._HOP_MONITOR_LOOP_CADENCE_S",
        0.05,
    )
    monkeypatch.setattr(
        "bus_watch.ide_context_hop_monitor.safe_emit_hop_monitor_tick",
        lambda **_kw: None,
    )

    emitted: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "bus_watch.events.emit_endpoint_wake_pass_duration",
        lambda **kw: emitted.append(dict(kw)),
    )

    disp_sleeps = {"n": 0}

    async def _sleep(delay: float) -> None:
        if delay >= 1.5:
            disp_sleeps["n"] += 1
            if disp_sleeps["n"] >= 3:
                raise asyncio.CancelledError()
            return

    monkeypatch.setattr(asyncio, "sleep", _sleep)

    async def _main() -> None:
        hop_task = asyncio.create_task(run_ide_context_hop_monitor())
        try:
            with pytest.raises(asyncio.CancelledError):
                await run_dispatcher()
        finally:
            hop_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await hop_task

    asyncio.run(_main())

    assert len(emitted) >= 2
    for row in emitted:
        assert row["total_pass_ms"] < 500.0
        assert row["check_cursor_ide_context_hops_ms"] == 0.0


def test_hop_monitor_continues_after_check_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bus_watch.ide_context_hop_monitor import run_ide_context_hop_monitor

    calls = {"n": 0}

    def _flaky_check(*_a: object, **_k: object) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("hop check failed")

    monkeypatch.setattr(
        "bus_watch.ide_context_hop_monitor.check_cursor_ide_context_hops",
        _flaky_check,
    )
    monkeypatch.setattr(
        "bus_watch.ide_context_hop_monitor._HOP_MONITOR_LOOP_CADENCE_S",
        0.01,
    )
    ticks: list[dict[str, str]] = []

    def _record_tick(**kw: object) -> None:
        ticks.append({"outcome": str(kw.get("outcome", ""))})

    monkeypatch.setattr(
        "bus_watch.ide_context_hop_monitor.safe_emit_hop_monitor_tick",
        _record_tick,
    )

    async def _run() -> None:
        task = asyncio.create_task(run_ide_context_hop_monitor())
        deadline = time.monotonic() + 0.5
        while calls["n"] < 2 and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_run())
    assert calls["n"] >= 2
    assert any(t["outcome"] == "error" for t in ticks)


def test_hop_monitor_skips_overlapping_check(monkeypatch: pytest.MonkeyPatch) -> None:
    from bus_watch.ide_context_hop_monitor import _guarded_hop_check

    monkeypatch.setattr(
        "bus_watch.ide_context_hop_monitor.check_cursor_ide_context_hops",
        lambda *_a, **_k: time.sleep(0.2),
    )
    store = object()
    mark: dict[str, float] = {}

    async def _two_threads() -> tuple[str, str]:
        return await asyncio.gather(
            asyncio.to_thread(_guarded_hop_check, store, mark),
            asyncio.to_thread(_guarded_hop_check, store, mark),
        )

    outcomes = asyncio.run(_two_threads())
    assert "skipped_overlap" in {o[0] for o in outcomes}
