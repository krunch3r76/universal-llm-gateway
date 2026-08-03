"""Regression: GIW lifespan must schedule persistence post-bind (hang class)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from services.git_integration_worker.startup_persistence import (
    run_startup_persistence,
    schedule_startup_persistence,
)


@pytest.mark.asyncio
async def test_schedule_startup_persistence_does_not_await_bundle() -> None:
    """schedule_* returns immediately; heavy work runs as a background task."""
    app = SimpleNamespace(state=SimpleNamespace())
    gate = asyncio.Event()

    async def _blocked(_app: object) -> None:
        await gate.wait()

    with patch(
        "services.git_integration_worker.startup_persistence.run_startup_persistence",
        new=_blocked,
    ):
        task = schedule_startup_persistence(app)
        assert app.state.startup_persistence_task is task
        assert app.state.startup_persistence_done is False
        # Must not block the caller (pre-yield hang class).
        await asyncio.sleep(0)
        assert not task.done()
        gate.set()
        await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_run_startup_persistence_marks_done_on_success() -> None:
    app = SimpleNamespace(state=SimpleNamespace())
    with (
        patch(
            "services.git_integration_worker.startup_persistence.startup_ledger_reconcile",
            new_callable=AsyncMock,
        ) as ledger,
        patch(
            "services.git_integration_worker.startup_persistence.startup_closeout_outbox_replay",
            new_callable=AsyncMock,
        ) as replay,
        patch(
            "services.git_integration_worker.startup_persistence.startup_auto_job_reconcile",
            new_callable=AsyncMock,
        ) as jobs,
    ):
        await run_startup_persistence(app)
    ledger.assert_awaited_once_with(app)
    replay.assert_awaited_once_with(app)
    jobs.assert_awaited_once_with(app)
    assert app.state.startup_persistence_done is True


@pytest.mark.asyncio
async def test_fetch_turns_from_single_bounded_tip_window() -> None:
    """GET /turns ignores after_turn — scan must not page-loop the tip."""
    from services.git_integration_worker.cursor_auto import closeout_bus_scan as scan

    calls: list[dict] = []

    class _Resp:
        status_code = 200

        def json(self) -> dict:
            return {
                "turns": [
                    {"turn_number": 10, "body": "new"},
                    {"turn_number": 5, "body": "mid"},
                    {"turn_number": 1, "body": "old"},
                ]
            }

    class _Client:
        async def get(self, *args, **kwargs):
            calls.append(kwargs.get("params") or {})
            return _Resp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    with patch(
        "services.git_integration_worker.cursor_auto.closeout_bus_scan.make_async_client",
        return_value=_Client(),
    ):
        turns, err = await scan.fetch_turns_from("6655", after_turn=5)
    assert err is None
    assert turns is not None
    assert [t["turn_number"] for t in turns] == [10, 5]
    assert len(calls) == 1
    assert "after_turn" not in calls[0]
    assert calls[0]["last"] == scan._MAX_TIP_WINDOW
