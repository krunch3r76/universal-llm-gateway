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
    wait_ok: dict[str, bool] = {}

    def _blocking_flush(*, store=None) -> int:
        started.set()
        wait_ok["released"] = release.wait(timeout=2.0)
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
        assert wait_ok.get("released") is True
        return other

    assert asyncio.run(_run()) == "other-ran"


@pytest.mark.asyncio
async def test_busy_status_schedules_flush_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """``busy_report`` (not a test double) must arm the owed-wake flush once per read."""
    from scripts.model_manager.ui import api_dispatch
    from scripts.model_manager.ui.controller.restart_drain import (
        ActiveWork,
        RestartDrainGate,
    )
    from scripts.model_manager.ui.controller.restart_intent_store import (
        RestartIntentStore,
    )

    class _Probe:
        def __init__(self, work: ActiveWork) -> None:
            self._work = work

        async def snapshot(self) -> ActiveWork:
            return self._work

    schedule_count = 0

    def _schedule(*, store=None) -> None:
        nonlocal schedule_count
        schedule_count += 1

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.owed_wake_ledger.schedule_owed_wake_flush",
        _schedule,
    )

    idle = ActiveWork(
        busy=False,
        detail={"busy": False, "total": 0, "active_count": 0},
    )
    store = RestartIntentStore(tmp_path / "restart-intents.db")
    ctl = MagicMock()
    ctl.restart_intent_store = store
    ctl.restart_gate = RestartDrainGate(
        probes={"git_integration_worker": _Probe(idle)}
    )
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
