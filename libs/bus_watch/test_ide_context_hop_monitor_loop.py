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


_UUID_A = "f19ca60c-27d7-43b0-bf7e-4e7d29a9eb03"
_UUID_B = "06e4b108-1ca1-4593-a172-37b0a40f39e4"
_UUID_C = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _houseless_sub_row(
    *,
    sub_id: int,
    scope: str = "24600",
    agent_id: str,
    bound_by: str = "cursor-grand-impresario",
) -> dict[str, Any]:
    return {
        "id": sub_id,
        "surface": "cursor",
        "thread_scope": scope,
        "display": "8",
        "state": "subscribed",
        "agent_id": agent_id,
        "bound_by": bound_by,
    }


@patch("bus_watch.ide_context_hop_monitor.measure_ide_tab")
@patch("bus_watch.ide_context_hop_monitor.read_lock")
def test_endpoint_subscription_samples_every_candidate_uuid(
    mock_lock: MagicMock,
    mock_measure: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bus_watch.ide_context_hop_monitor import check_cursor_ide_context_hops

    mock_lock.return_value = {}
    mock_measure.side_effect = lambda _root, _lock, _policy, **kw: {
        "transcript_id": kw.get("transcript_id"),
        "holder_basis": "endpoint_subscription",
        "used_tokens": 10_000,
        "window_limit_tokens": 256_000,
        "tool_calls": 1,
        "bytes": 100,
        "budget_probe": "glass.cdp",
        "glass_status": "completed",
    }
    emitted: list[dict] = []
    monkeypatch.setattr(
        "bus_watch.events.emit_ide_tab_budget_sampled",
        lambda **kw: emitted.append(dict(kw)),
    )
    monkeypatch.setattr(
        "bus_watch.ide_context_hop_monitor._all_cursor_subscription_rows",
        lambda _store: [
            _houseless_sub_row(sub_id=1, agent_id=_UUID_A),
            _houseless_sub_row(sub_id=2, agent_id=_UUID_B),
            _houseless_sub_row(sub_id=3, agent_id=_UUID_C),
        ],
    )
    store = MagicMock()
    store.subscriptions.return_value = [
        _houseless_sub_row(sub_id=1, agent_id=_UUID_A),
        _houseless_sub_row(sub_id=2, agent_id=_UUID_B),
        _houseless_sub_row(sub_id=3, agent_id=_UUID_C),
    ]
    check_cursor_ide_context_hops(store, mark={}, now=1000.0)
    tids = sorted(row["transcript_id"] for row in emitted)
    assert tids == sorted([_UUID_A, _UUID_B, _UUID_C])
    assert all(row["holder_basis"] == "endpoint_subscription" for row in emitted)


@patch("bus_watch.ide_context_hop_monitor.measure_ide_tab")
@patch("bus_watch.ide_context_hop_monitor.read_lock")
def test_actuation_refused_does_not_suppress_sample(
    mock_lock: MagicMock,
    mock_measure: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bus_watch.ide_context_hop_monitor import check_cursor_ide_context_hops

    mock_lock.return_value = {}
    mock_measure.return_value = {
        "transcript_id": _UUID_A,
        "holder_basis": "endpoint_subscription",
        "used_tokens": 10_000,
        "window_limit_tokens": 256_000,
        "tool_calls": 1,
        "bytes": 50,
        "budget_probe": "glass.cdp",
        "glass_status": "completed",
    }
    emitted: list[dict] = []
    monkeypatch.setattr(
        "bus_watch.events.emit_ide_tab_budget_sampled",
        lambda **kw: emitted.append(dict(kw)),
    )
    monkeypatch.setattr(
        "bus_watch.ide_context_hop_monitor._all_cursor_subscription_rows",
        lambda _store: [_houseless_sub_row(sub_id=1, agent_id=_UUID_A)],
    )
    store = MagicMock()
    store.subscriptions.return_value = [
        _houseless_sub_row(sub_id=1, agent_id=_UUID_A, bound_by="other-seat"),
    ]
    check_cursor_ide_context_hops(store, mark={}, now=1000.0)
    assert len(emitted) == 1
    assert emitted[0]["actuation_refused"] == "no_live_claim"


def test_stale_refusal_includes_row_older_than_successor() -> None:
    from bus_watch.ide_context_hop_monitor import row_stale_vs_successor

    older = _houseless_sub_row(sub_id=10, agent_id=_UUID_A)
    newer = _houseless_sub_row(sub_id=20, agent_id=_UUID_B)
    rows = [older, newer]
    assert row_stale_vs_successor(older, rows) is True
    assert row_stale_vs_successor(newer, rows) is False


def test_resolve_scope_actuation_ambiguous_two_stamped_claimed_rows() -> None:
    from bus_watch.ide_context_hop_monitor import resolve_scope_actuation_refusal

    older = _houseless_sub_row(sub_id=10, agent_id=_UUID_A)
    newer = _houseless_sub_row(sub_id=20, agent_id=_UUID_A)
    rows = [older, newer]
    refusal, eligible = resolve_scope_actuation_refusal(
        rows,
        rows_same_scope=rows,
        claim_tab_uuid_for_scope=lambda _scope: _UUID_A,
    )
    assert refusal == "ambiguous"
    assert eligible is False


@patch("bus_watch.ide_context_hop_monitor.measure_ide_tab")
@patch("bus_watch.ide_context_hop_monitor.read_lock")
def test_transcript_estimate_probe_not_actuation_eligible(
    mock_lock: MagicMock,
    mock_measure: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bus_watch.ide_context_hop_monitor import (
        _GI_TRIAL_STAMP,
        check_cursor_ide_context_hops,
    )

    mock_lock.return_value = {}
    mock_measure.return_value = {
        "transcript_id": _UUID_A,
        "holder_basis": "endpoint_subscription",
        "used_tokens": 80_000,
        "window_limit_tokens": 256_000,
        "tool_calls": 40,
        "bytes": 50,
        "budget_probe": "transcript_estimate",
        "glass_summarized_tokens": 0,
    }
    emitted: list[dict] = []
    monkeypatch.setattr(
        "bus_watch.events.emit_ide_tab_budget_sampled",
        lambda **kw: emitted.append(dict(kw)),
    )
    row = _houseless_sub_row(sub_id=1, agent_id=_UUID_A, bound_by=_GI_TRIAL_STAMP)
    monkeypatch.setattr(
        "bus_watch.ide_context_hop_monitor._all_cursor_subscription_rows",
        lambda _store: [row],
    )
    store = MagicMock()
    store.subscriptions.return_value = [row]
    with patch(
        "bus_watch.ide_context_hop_monitor.resolve_scope_actuation_refusal",
        return_value=(None, True),
    ):
        check_cursor_ide_context_hops(store, mark={}, now=1000.0)
    assert len(emitted) == 1
    assert emitted[0]["budget_probe"] == "transcript_estimate"
    assert emitted[0]["actuation_eligible"] is False


@patch("bus_watch.ide_context_hop_monitor.measure_ide_tab")
@patch("bus_watch.ide_context_hop_monitor.read_lock")
def test_glass_cdp_probe_can_be_actuation_eligible(
    mock_lock: MagicMock,
    mock_measure: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bus_watch.ide_context_hop_monitor import (
        _GI_TRIAL_STAMP,
        check_cursor_ide_context_hops,
    )

    mock_lock.return_value = {}
    mock_measure.return_value = {
        "transcript_id": _UUID_A,
        "holder_basis": "endpoint_subscription",
        "used_tokens": 80_000,
        "window_limit_tokens": 256_000,
        "tool_calls": 2,
        "bytes": 50,
        "budget_probe": "glass.cdp",
        "glass_status": "completed",
        "glass_summarized_tokens": 100,
    }
    emitted: list[dict] = []
    monkeypatch.setattr(
        "bus_watch.events.emit_ide_tab_budget_sampled",
        lambda **kw: emitted.append(dict(kw)),
    )
    row = _houseless_sub_row(sub_id=1, agent_id=_UUID_A, bound_by=_GI_TRIAL_STAMP)
    monkeypatch.setattr(
        "bus_watch.ide_context_hop_monitor._all_cursor_subscription_rows",
        lambda _store: [row],
    )
    store = MagicMock()
    store.subscriptions.return_value = [row]
    with patch(
        "bus_watch.ide_context_hop_monitor.resolve_scope_actuation_refusal",
        return_value=(None, True),
    ):
        check_cursor_ide_context_hops(store, mark={}, now=1000.0)
    assert len(emitted) == 1
    assert emitted[0]["actuation_eligible"] is True
    assert emitted[0]["used_percent_at_first_summarized"] == pytest.approx(
        100.0 * 80_000 / 256_000, rel=0.01
    )


@patch("bus_watch.ide_context_hop_monitor.measure_ide_tab")
@patch("bus_watch.ide_context_hop_monitor.read_lock")
def test_emit_latches_used_percent_at_first_summarized_across_samples(
    mock_lock: MagicMock,
    mock_measure: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bus_watch.digest_budget import reset_used_percent_at_first_summarized_latch
    from bus_watch.ide_context_hop_monitor import (
        _GI_TRIAL_STAMP,
        check_cursor_ide_context_hops,
    )

    reset_used_percent_at_first_summarized_latch()
    mock_lock.return_value = {}
    base_measure = {
        "transcript_id": _UUID_A,
        "holder_basis": "endpoint_subscription",
        "window_limit_tokens": 256_000,
        "tool_calls": 2,
        "bytes": 50,
        "budget_probe": "glass.cdp",
        "glass_status": "completed",
    }
    mock_measure.side_effect = [
        {**base_measure, "used_tokens": 145_000, "glass_summarized_tokens": 100},
        {**base_measure, "used_tokens": 80_000, "glass_summarized_tokens": 500},
    ]
    emitted: list[dict] = []
    monkeypatch.setattr(
        "bus_watch.events.emit_ide_tab_budget_sampled",
        lambda **kw: emitted.append(dict(kw)),
    )
    row = _houseless_sub_row(sub_id=1, agent_id=_UUID_A, bound_by=_GI_TRIAL_STAMP)
    monkeypatch.setattr(
        "bus_watch.ide_context_hop_monitor._all_cursor_subscription_rows",
        lambda _store: [row],
    )
    store = MagicMock()
    store.subscriptions.return_value = [row]
    with patch(
        "bus_watch.ide_context_hop_monitor.resolve_scope_actuation_refusal",
        return_value=(None, True),
    ):
        mark: dict[str, float] = {}
        check_cursor_ide_context_hops(store, mark=mark, now=1000.0)
        check_cursor_ide_context_hops(store, mark=mark, now=2000.0)
    assert len(emitted) == 2
    latched = round(100.0 * 145_000 / 256_000, 2)
    assert emitted[0]["used_percent_at_first_summarized"] == pytest.approx(
        latched, rel=0.01
    )
    assert emitted[1]["used_percent_at_first_summarized"] == pytest.approx(
        latched, rel=0.01
    )
