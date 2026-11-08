"""Background ide_context_hop_monitor loop — decoupled from wake passes."""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any
from unittest.mock import MagicMock, patch

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
    monkeypatch.setattr(
        "bus_watch.ide_context_hop_monitor.StoreClient",
        MagicMock,
    )

    entered = threading.Event()
    release = threading.Event()

    def _slow_check(*_a: object, **_k: object) -> None:
        entered.set()
        release.wait(timeout=5.0)

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
    real_sleep = asyncio.sleep

    async def _sleep(delay: float) -> None:
        if delay >= 1.5:
            disp_sleeps["n"] += 1
            if disp_sleeps["n"] >= 3:
                raise asyncio.CancelledError()
            return
        await real_sleep(min(delay, 0.01))

    monkeypatch.setattr(asyncio, "sleep", _sleep)

    async def _main() -> None:
        hop_task = asyncio.create_task(run_ide_context_hop_monitor())
        deadline = time.monotonic() + 2.0
        while not entered.is_set() and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert entered.is_set(), "hop monitor did not enter slow check"
        try:
            with pytest.raises(asyncio.CancelledError):
                await run_dispatcher()
        finally:
            release.set()
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


def test_guarded_hop_check_releases_lock_after_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bus_watch.ide_context_hop_monitor import _check_in_flight, _guarded_hop_check

    monkeypatch.setattr(
        "bus_watch.ide_context_hop_monitor.check_cursor_ide_context_hops",
        lambda *_a, **_k: None,
    )
    _guarded_hop_check(MagicMock(), {})
    assert _check_in_flight.acquire(blocking=False)
    _check_in_flight.release()


def test_guarded_hop_check_releases_lock_after_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bus_watch.ide_context_hop_monitor import _check_in_flight, _guarded_hop_check

    def _boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("check exploded")

    monkeypatch.setattr(
        "bus_watch.ide_context_hop_monitor.check_cursor_ide_context_hops",
        _boom,
    )
    outcome, _ms = _guarded_hop_check(MagicMock(), {})
    assert outcome == "error"
    assert _check_in_flight.acquire(blocking=False)
    _check_in_flight.release()


@pytest.mark.parametrize("emit_raises", [False, True])
@patch("bus_watch.ide_context_hop_monitor._run_ide_hop")
@patch("bus_watch.ide_context_hop_monitor._budget_for_root")
def test_hop_monitor_emits_tab_budget_sample(
    mock_budget: MagicMock,
    mock_hop: MagicMock,
    emit_raises: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bus_watch.ide_context_hop_monitor import check_cursor_ide_context_hops

    budget = {
      "stop_class": "CONTEXT_BUDGET",
      "transcript_id": "tid-1",
      "tool_calls": 40,
      "tool_result_tokens": 50_000,
      "used_tokens": 10_000,
      "window_limit_tokens": 256_000,
      "hop_weight_basis": "tool_result_tokens",
      "glass_status": "completed",
    }
    mock_budget.return_value = budget
    mock_hop.return_value = MagicMock(returncode=0, stdout="", stderr="")
    emitted: list[dict] = []
    hop_calls: list[object] = []

    def _record_emit(**kw: object) -> None:
        emitted.append(dict(kw))
        if emit_raises:
            raise RuntimeError("emit failed")

    def _hop(*_a: object, **_k: object) -> MagicMock:
        hop_calls.append(True)
        return mock_hop.return_value

    monkeypatch.setattr(
        "bus_watch.events.emit_ide_tab_budget_sampled",
        _record_emit,
    )
    monkeypatch.setattr(
        "bus_watch.ide_context_hop_monitor._run_ide_hop",
        _hop,
    )
    store = MagicMock()
    store.subscriptions.return_value = [
        {
            "surface": "cursor",
            "thread_scope": "18291",
            "display": "8",
            "state": "subscribed",
        }
    ]
    mark: dict[str, float] = {}
    check_cursor_ide_context_hops(store, mark=mark, now=1000.0)
    assert len(emitted) == 1
    assert emitted[0]["root_id"] == "18291"
    assert emitted[0]["hop_weight_basis"] == "tool_result_tokens"
    assert len(hop_calls) == 1
    assert hop_calls, "hop should run after sample emit"


@patch("bus_watch.ide_context_hop_monitor._run_ide_hop")
@patch("bus_watch.ide_context_hop_monitor._budget_for_root")
def test_tab_budget_sample_matched_categories(
    mock_budget: MagicMock,
    mock_hop: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bus_watch.ide_context_hop_monitor import check_cursor_ide_context_hops

    mock_budget.return_value = {
        "stop_class": None,
        "transcript_id": "tid-2",
        "tool_calls": 2,
        "tool_result_tokens": 18_000,
        "matched_categories": ["tools"],  # from prompt_breakdown tools/conversation
        "used_tokens": 5000,
        "window_limit_tokens": 256_000,
        "hop_weight_basis": "tool_result_tokens",
        "glass_status": "completed",
    }
    mock_hop.return_value = MagicMock(returncode=0, stdout="", stderr="")
    emitted: list[dict] = []
    monkeypatch.setattr(
        "bus_watch.events.emit_ide_tab_budget_sampled",
        lambda **kw: emitted.append(dict(kw)),
    )
    store = MagicMock()
    store.subscriptions.return_value = [
        {
            "surface": "cursor",
            "thread_scope": "18291",
            "display": "8",
            "state": "subscribed",
        }
    ]
    check_cursor_ide_context_hops(store, mark={}, now=1000.0)
    assert len(emitted) == 1
    assert emitted[0]["matched_categories"] == ["tools"]


@patch("bus_watch.ide_context_hop_monitor._budget_for_root")
def test_root_rechecked_at_60s_not_delayed_past_next_tick(
    mock_budget: MagicMock,
) -> None:
    """Loop cadence 20s + per-root 60s: due at t+60, not t+120."""
    from bus_watch.ide_context_hop_monitor import (
        _HOP_CHECK_INTERVAL_S,
        _HOP_MONITOR_LOOP_CADENCE_S,
        check_cursor_ide_context_hops,
    )

    assert _HOP_MONITOR_LOOP_CADENCE_S < _HOP_CHECK_INTERVAL_S
    mock_budget.return_value = None
    store = MagicMock()
    store.subscriptions.return_value = [
        {
            "surface": "cursor",
            "thread_scope": "18291",
            "display": "8",
            "state": "subscribed",
        }
    ]
    mark: dict[str, float] = {}
    t0 = 1000.0
    check_cursor_ide_context_hops(store, mark=mark, now=t0)
    assert mock_budget.call_count == 1
    check_cursor_ide_context_hops(store, mark=mark, now=t0 + 59.9)
    assert mock_budget.call_count == 1
    check_cursor_ide_context_hops(store, mark=mark, now=t0 + 60.0)
    assert mock_budget.call_count == 2
