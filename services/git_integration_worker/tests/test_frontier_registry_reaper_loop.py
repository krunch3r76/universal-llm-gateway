"""Async loop tests for ``frontier_registry_reaper`` and stale-lease stall visibility."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_work_registry_admit import (
    upsert_frontier_registry_dispatch,
)
from services.git_integration_worker.routes import cursor_sdk as cursor_sdk_mod
from services.git_integration_worker.routes.cursor_sdk import (
    frontier_registry_reaper,
    stale_lease_sweeper,
)

_SWEEP_S = 0.05


@pytest.fixture
def isolated_registry_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "registry.sqlite"
    monkeypatch.setenv("CURSOR_SDK_DISPATCH_LEDGER", str(db))
    CursorDispatchLedger._instance = None
    return CursorDispatchLedger.instance()


@pytest.fixture
def fast_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cursor_sdk_mod, "_STALE_SWEEP_S", _SWEEP_S)


def _stale_frontier_row(
    ledger: CursorDispatchLedger,
    dispatch_id: str = "stale-api-loop",
) -> None:
    stale = (datetime.now(UTC) - timedelta(hours=5)).isoformat()
    upsert_frontier_registry_dispatch(
        dispatch_id=dispatch_id,
        thread_id="21802",
        caller_agent="maestro",
        seat_name="maestro",
        address={"kind": "api_role", "thread_id": "21802"},
        work_key=f"todo:{dispatch_id}",
        ledger=ledger,
    )
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET started_at=?, queued_at=? "
            "WHERE dispatch_id=?",
            (stale, stale, dispatch_id),
        )
        conn.commit()


async def _cancel_task(task: asyncio.Task[None]) -> None:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_reaper_fails_stale_row_while_reconcile_hung(
    isolated_registry_ledger: CursorDispatchLedger,
    fast_sweep: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hung ``reconcile_stale_leases`` must not block the frontier TTL reaper."""

    async def _reconcile_never_returns(*_args, **_kwargs) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(
        cursor_sdk_mod, "reconcile_stale_leases", _reconcile_never_returns
    )
    _stale_frontier_row(isolated_registry_ledger)
    app = FastAPI()
    controller = MagicMock()
    controller.is_draining.return_value = False
    app.state.admission_controller = controller

    reaper = asyncio.create_task(frontier_registry_reaper(app))
    sweeper = asyncio.create_task(stale_lease_sweeper(app))
    try:
        await asyncio.sleep(2 * _SWEEP_S + _SWEEP_S)
        with isolated_registry_ledger._connect() as conn:
            row = conn.execute(
                "SELECT status FROM cursor_sdk_dispatches WHERE dispatch_id=?",
                ("stale-api-loop",),
            ).fetchone()
        assert row["status"] == "failed"
    finally:
        await _cancel_task(reaper)
        await _cancel_task(sweeper)


@pytest.mark.asyncio
async def test_reaper_logs_stall_when_reconcile_open_too_long(
    fast_sweep: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = FastAPI()
    sweep_s = cursor_sdk_mod._STALE_SWEEP_S
    started = datetime.now(UTC) - timedelta(seconds=11 * sweep_s)
    app.state.stale_lease_sweep_started_at = started
    app.state.stale_lease_sweep_finished_at = None

    reaper = asyncio.create_task(frontier_registry_reaper(app))
    try:
        with caplog.at_level(logging.WARNING):
            await asyncio.sleep(2 * sweep_s + sweep_s)
        stall_lines = [
            r.message
            for r in caplog.records
            if r.message.startswith("stale-lease reconcile stalled:")
        ]
        assert len(stall_lines) == 1
    finally:
        await _cancel_task(reaper)


@pytest.mark.asyncio
async def test_reaper_leaves_fresh_frontier_row_admitted(
    isolated_registry_ledger: CursorDispatchLedger,
    fast_sweep: None,
) -> None:
    upsert_frontier_registry_dispatch(
        dispatch_id="fresh-api-loop",
        thread_id="21802",
        caller_agent="maestro",
        seat_name="maestro",
        address={"kind": "api_role", "thread_id": "21802"},
        work_key="todo:fresh-loop",
        ledger=isolated_registry_ledger,
    )
    app = FastAPI()
    reaper = asyncio.create_task(frontier_registry_reaper(app))
    try:
        await asyncio.sleep(3 * _SWEEP_S)
        with isolated_registry_ledger._connect() as conn:
            row = conn.execute(
                "SELECT status FROM cursor_sdk_dispatches WHERE dispatch_id=?",
                ("fresh-api-loop",),
            ).fetchone()
        assert row["status"] == "admitted"
    finally:
        await _cancel_task(reaper)


def test_frontier_registry_reaper_supervised_with_restart() -> None:
    app_py = Path("services/git_integration_worker/app.py").read_text(encoding="utf-8")
    assert re.search(
        r'supervise\(\s*\n\s*app,\s*\n\s*"frontier_registry_reaper"[\s\S]*?restart=True',
        app_py,
    )
