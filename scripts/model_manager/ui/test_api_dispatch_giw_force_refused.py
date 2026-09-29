"""Refuse force=true on git_integration_worker lifecycle in api_dispatch."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from scripts.model_manager.ui import api_dispatch

pytestmark = pytest.mark.offline


def _run(coro):
    return asyncio.run(coro)


def test_restart_giw_force_refused_does_not_call_run_gated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _run_gated(*_args, **_kwargs):
        raise AssertionError("run_gated must not run when force is refused")

    monkeypatch.setattr(api_dispatch, "run_gated", _run_gated)
    monkeypatch.setattr(
        api_dispatch,
        "preempt_giw_keep_await_if_needed",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        api_dispatch,
        "_git_worker_drain_supervised",
        AsyncMock(
            side_effect=AssertionError("_git_worker_drain_supervised must not run")
        ),
    )
    ctl = MagicMock()
    result = _run(
        api_dispatch.execute(
            ctl,
            "restart",
            "git_integration_worker",
            {"force": True},
        )
    )
    assert result["reason"] == "giw_force_refused"
    assert result == api_dispatch.refuse_giw_force_lifecycle()


def test_restart_mcp_force_still_uses_deferred_sync_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deferred = {"status": "deferred", "service": "mcp"}

    async def _mcp_deferred(*_args, **_kwargs):
        return deferred

    monkeypatch.setattr(api_dispatch, "_mcp_deferred_sync_restart", _mcp_deferred)
    monkeypatch.setattr(
        api_dispatch,
        "_finalize_restart_rebuild",
        AsyncMock(return_value={"status": "ok", "nested": deferred}),
    )
    ctl = MagicMock()
    result = _run(
        api_dispatch.execute(
            ctl,
            "restart",
            "mcp",
            {"force": True},
        )
    )
    assert result["status"] == "ok"
