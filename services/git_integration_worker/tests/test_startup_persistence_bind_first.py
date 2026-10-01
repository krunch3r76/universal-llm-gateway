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
    """Ledger reconcile plus the key probe; Auto replay and auto-job reconcile are gone."""
    app = SimpleNamespace(state=SimpleNamespace())
    with (
        patch(
            "services.git_integration_worker.startup_persistence.startup_ledger_reconcile",
            new_callable=AsyncMock,
        ) as ledger,
        patch(
            "services.git_integration_worker.cursor_sdk_key_entitlement.probe_configured_keys",
            return_value=[],
        ) as keys,
    ):
        await run_startup_persistence(app)
    ledger.assert_awaited_once_with(app)
    keys.assert_called_once_with()
    assert app.state.startup_persistence_done is True
