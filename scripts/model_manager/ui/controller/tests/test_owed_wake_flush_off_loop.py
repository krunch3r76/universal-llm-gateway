"""Owed-wake flush must not block manage's asyncio event loop."""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import MagicMock

import pytest

from scripts.model_manager.ui.controller.owed_wake_ledger import (
    schedule_owed_wake_flush,
)


def test_blocking_flush_lets_other_coroutines_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()
    started = threading.Event()

    def _blocking_flush(*, store=None) -> int:
        started.set()
        release.wait(timeout=2.0)
        return 0

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.owed_wake_ledger.maybe_flush_pending_owed_wakes",
        _blocking_flush,
    )

    async def _concurrent_sleeper() -> str:
        await asyncio.sleep(0.05)
        assert started.is_set()
        release.set()
        return "other-ran"

    async def _run() -> str:
        from scripts.model_manager.ui.controller import owed_wake_ledger as mod

        schedule_owed_wake_flush()
        task = mod._owed_wake_flush_task
        assert task is not None
        _, other = await asyncio.gather(task, _concurrent_sleeper())
        return other

    assert asyncio.run(_run()) == "other-ran"


@pytest.mark.asyncio
async def test_busy_status_schedules_flush_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.model_manager.ui import api_dispatch

    schedule_count = 0

    def _schedule(*, store=None) -> None:
        nonlocal schedule_count
        schedule_count += 1

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.owed_wake_ledger.schedule_owed_wake_flush",
        _schedule,
    )
    store = MagicMock()
    store.sweep_expired_windows.return_value = None
    store.pending_intents.return_value = []
    store.restart_window_for_service.return_value = None

    async def _busy_report(services):
        import scripts.model_manager.ui.controller.owed_wake_ledger as owl

        owl.schedule_owed_wake_flush()
        return {
            svc: {
                "busy": False,
                "restart_would_defer": False,
                "determination": "idle",
                "active_work": {"busy": False, "total": 0},
            }
            for svc in services
        }

    ctl = MagicMock()
    ctl.restart_intent_store = store
    ctl.restart_gate.busy_report = _busy_report
    ctl.build_running = False
    ctl.shutdown_gate.snapshot.return_value = MagicMock(
        manage_inflight=0, activities=[]
    )

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.restart_intent_lookup.latest_terminal_for_service",
        lambda _store, _svc, **_: None,
    )

    await api_dispatch._busy_status(ctl, service="git_integration_worker")
    assert schedule_count == 1
