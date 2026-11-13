"""Tests for endpoint wake dispatcher per-pass duration observation."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest


def test_run_dispatcher_emits_one_pass_duration_per_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bus_watch.endpoint_wake_dispatcher import run_dispatcher

    emitted: list[dict[str, Any]] = []

    def _noop(*_a: object, **_k: object) -> None:
        return None

    def _fire_returns_none(*_a: object, **_k: object) -> None:
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
    monkeypatch.setattr(
        "bus_watch.events.emit_endpoint_wake_pass_duration",
        lambda **kw: emitted.append(dict(kw)),
    )

    sleeps = {"n": 0}

    async def _sleep(_delay: float) -> None:
        sleeps["n"] += 1
        if sleeps["n"] >= 2:
            raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", _sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run_dispatcher())

    assert len(emitted) == 2
    for row in emitted:
        assert "total_pass_ms" in row
        assert row["fire_due_wakes_iteration_count"] == 1
        assert row["display_post_count"] == 0
        assert row["display_post_cumulative_ms"] == 0.0
        for phase in (
            "normalize_legacy_pending_wakes_ms",
            "escalate_composer_deferred_on_sweep_ms",
            "expire_overdue_hops_ms",
            "scan_once_ms",
            "fire_due_wakes_ms",
            "finalize_composer_defer_pass_ms",
            "check_cursor_ide_context_hops_ms",
            "run_patrol_pass_ms",
        ):
            assert phase in row
            assert row[phase] >= 0.0


def test_pass_duration_emit_error_does_not_break_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bus_watch.endpoint_wake_dispatcher import run_dispatcher

    def _noop(*_a: object, **_k: object) -> None:
        return None

    def _emit_boom(**_kw: object) -> None:
        raise OSError("event service down")

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
    monkeypatch.setattr(
        "bus_watch.events.emit_endpoint_wake_pass_duration",
        _emit_boom,
    )

    sleeps = {"n": 0}

    async def _sleep(_delay: float) -> None:
        sleeps["n"] += 1
        if sleeps["n"] >= 1:
            raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", _sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run_dispatcher())
    assert sleeps["n"] == 1


def test_pass_duration_emits_partial_phase_timing_on_stage_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bus_watch.endpoint_wake_dispatcher import run_dispatcher

    emitted: list[dict[str, Any]] = []

    def _scan_boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("scan failed")

    monkeypatch.setattr(
        "bus_watch.endpoint_wake_composer_defer.normalize_legacy_pending_wakes",
        lambda: None,
    )
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_composer_defer.escalate_composer_deferred_on_sweep",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.expire_overdue_hops",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.scan_once",
        _scan_boom,
    )
    monkeypatch.setattr(
        "bus_watch.events.emit_endpoint_wake_pass_duration",
        lambda **kw: emitted.append(dict(kw)),
    )
    monkeypatch.setattr(
        "bus_watch.events.emit_endpoint_wake_pass_failed",
        lambda **_kw: None,
    )

    async def _sleep(_delay: float) -> None:
        raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", _sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run_dispatcher())

    assert len(emitted) == 1
    row = emitted[0]
    assert row["scan_once_ms"] >= 0.0
    assert row["fire_due_wakes_ms"] == 0.0
    assert row["fire_due_wakes_iteration_count"] == 0


def test_display_post_records_cumulative_duration() -> None:
    from bus_watch import endpoint_wake_dispatcher as disp
    from bus_watch.endpoint_wake_pass_timing import (
        begin_pass,
        build_pass_duration_payload,
        reset_pass_timing_for_tests,
    )

    reset_pass_timing_for_tests()
    begin_pass()
    with patch.object(
        disp,
        "_display_post_body",
        return_value=("delivered", "ok", None),
    ):
        disp._display_post({}, {}, 1)
        disp._display_post({}, {}, 2)
    payload = build_pass_duration_payload()
    assert payload is not None
    assert payload["display_post_count"] == 2
    assert payload["display_post_cumulative_ms"] >= 0.0
