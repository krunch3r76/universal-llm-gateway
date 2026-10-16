"""RAG sync_restart settle hold — friction a:39394 plan r2."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scripts.model_manager.ui import api_dispatch
from scripts.model_manager.ui.controller.restart_drain import (
    RestartDrainGate,
    run_gated,
)

pytestmark = pytest.mark.offline

RAG_MSG = "RAG service starting (PID 4242)."


def _run(coro):
    return asyncio.run(coro)


def _gate() -> RestartDrainGate:
    return RestartDrainGate(probes={})


async def _run_gated_with_hook(
    gate: RestartDrainGate,
    before_release,
    *,
    lifecycle_message: str = RAG_MSG,
    lifecycle: AsyncMock | None = None,
) -> dict[str, Any]:
    if lifecycle is None:
        lifecycle = AsyncMock(return_value=lifecycle_message)
    return await run_gated(
        gate,
        "sync_restart",
        "rag",
        force=False,
        lifecycle=lifecycle,
        before_release=before_release,
    )


def test_run_gated_release_after_settle_ready() -> None:
    """UDS live and pid alive ends the settle hold and releases the slot."""
    gate = _gate()

    async def before_release(message: str) -> None:
        await api_dispatch._rag_restart_settle_before_release(message)

    with patch.object(api_dispatch, "_pid_alive", return_value=True):
        with patch(
            "scripts.model_manager.ui.controller.service_ctl.startup_probe.uds_socket_live",
            return_value=True,
        ):
            result = _run(_run_gated_with_hook(gate, before_release))
    assert result["status"] == "ok"
    assert not gate.restart_in_progress("rag")


def test_run_gated_release_after_settle_budget_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = _gate()
    monkeypatch.setattr(api_dispatch, "RAG_RESTART_SETTLE_BUDGET_S", 0.05)
    monkeypatch.setattr(
        api_dispatch,
        "_rag_restart_settle_before_release",
        api_dispatch._rag_restart_settle_before_release,
    )

    async def before_release(message: str) -> None:
        await api_dispatch._rag_restart_settle_before_release(message)

    with patch.object(api_dispatch, "_pid_alive", return_value=True):
        with patch(
            "scripts.model_manager.ui.controller.service_ctl.startup_probe.uds_socket_live",
            return_value=False,
        ):
            _run(_run_gated_with_hook(gate, before_release))
    assert not gate.restart_in_progress("rag")


def test_run_gated_release_after_new_pid_dead(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = _gate()

    async def before_release(message: str) -> None:
        await api_dispatch._rag_restart_settle_before_release(message)

    with patch.object(api_dispatch, "_pid_alive", return_value=False):
        _run(_run_gated_with_hook(gate, before_release))
    assert not gate.restart_in_progress("rag")


def test_run_gated_release_after_exception_in_before_release() -> None:
    gate = _gate()

    async def before_release(_message: str) -> None:
        raise RuntimeError("hold failed")

    with pytest.raises(RuntimeError, match="hold failed"):
        _run(_run_gated_with_hook(gate, before_release))
    assert not gate.restart_in_progress("rag")


def test_run_gated_release_after_cancelled_in_before_release() -> None:
    gate = _gate()

    async def before_release(_message: str) -> None:
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        _run(_run_gated_with_hook(gate, before_release))
    assert not gate.restart_in_progress("rag")


def test_second_rag_sync_restart_during_hold_is_in_progress_with_pid() -> None:
    gate = _gate()
    hold_entered = asyncio.Event()
    release_hold = asyncio.Event()

    async def before_release(_message: str) -> None:
        hold_entered.set()
        await release_hold.wait()

    async def _exercise() -> None:
        first = asyncio.create_task(
            _run_gated_with_hook(gate, before_release)
        )
        await hold_entered.wait()
        second = await run_gated(
            gate,
            "sync_restart",
            "rag",
            force=False,
            lifecycle=AsyncMock(
                side_effect=AssertionError("second lifecycle must not run")
            ),
        )
        release_hold.set()
        await first
        assert second["status"] == "deferred"
        assert second["state"] == "in_progress"
        assert "4242" in second["reason"]
        assert second.get("active_work", {}).get("in_flight_pid") == 4242

    _run(_exercise())


def test_rag_sync_restart_after_ready_runs_lifecycle_again() -> None:
    gate = _gate()
    lifecycle = AsyncMock(return_value=RAG_MSG)

    async def _exercise() -> None:
        first = await run_gated(
            gate,
            "sync_restart",
            "rag",
            force=False,
            lifecycle=lifecycle,
        )
        assert first["status"] == "ok"
        second = await run_gated(
            gate,
            "sync_restart",
            "rag",
            force=False,
            lifecycle=lifecycle,
        )
        assert second["status"] == "ok"
        assert lifecycle.await_count == 2

    _run(_exercise())


def test_rag_force_true_skips_settle_before_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settle = AsyncMock()

    async def _run_gated(*args, **kwargs):
        assert kwargs.get("before_release") is None
        return {"status": "ok", "message": RAG_MSG}

    monkeypatch.setattr(api_dispatch, "run_gated", _run_gated)
    monkeypatch.setattr(
        api_dispatch,
        "preempt_giw_keep_await_if_needed",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        api_dispatch,
        "preempt_stargate_idle_drain_if_needed",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(api_dispatch, "_rag_restart_settle_before_release", settle)
    ctl = MagicMock()
    result = _run(
        api_dispatch.execute(ctl, "sync_restart", "rag", {"force": True})
    )
    assert result["status"] == "ok"
    settle.assert_not_called()


def test_rag_force_true_during_existing_hold_stays_in_progress() -> None:
    gate = _gate()
    hold_entered = asyncio.Event()
    release_hold = asyncio.Event()

    async def before_release(_message: str) -> None:
        hold_entered.set()
        await release_hold.wait()

    async def _exercise() -> None:
        first = asyncio.create_task(
            _run_gated_with_hook(gate, before_release)
        )
        await hold_entered.wait()
        second = await run_gated(
            gate,
            "sync_restart",
            "rag",
            force=True,
            lifecycle=AsyncMock(
                side_effect=AssertionError("force must not preempt held slot")
            ),
        )
        release_hold.set()
        await first
        assert second["status"] == "deferred"
        assert second["state"] == "in_progress"

    _run(_exercise())


def test_stargate_sync_restart_does_not_pass_before_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def _run_gated(*_args, **kwargs):
        captured.update(kwargs)
        return {"status": "ok", "message": "Stargate starting (PID 1)."}

    monkeypatch.setattr(api_dispatch, "run_gated", _run_gated)
    monkeypatch.setattr(
        api_dispatch,
        "_stargate_idle_drain_supervised",
        AsyncMock(
            side_effect=AssertionError("stargate must use idle drain when not force")
        ),
    )
    monkeypatch.setattr(
        api_dispatch,
        "preempt_giw_keep_await_if_needed",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        api_dispatch,
        "preempt_stargate_idle_drain_if_needed",
        AsyncMock(return_value=None),
    )
    ctl = MagicMock()
    _run(api_dispatch.execute(ctl, "sync_restart", "stargate", {"force": True}))
    assert "before_release" not in captured or captured.get("before_release") is None


def test_giw_force_refused_does_not_invoke_rag_settle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settle = AsyncMock(side_effect=AssertionError("settle must not run for giw"))

    monkeypatch.setattr(api_dispatch, "_rag_restart_settle_before_release", settle)
    monkeypatch.setattr(
        api_dispatch,
        "run_gated",
        AsyncMock(side_effect=AssertionError("run_gated must not run")),
    )
    monkeypatch.setattr(
        api_dispatch,
        "preempt_giw_keep_await_if_needed",
        AsyncMock(return_value=None),
    )
    ctl = MagicMock()
    result = _run(
        api_dispatch.execute(
            ctl, "sync_restart", "git_integration_worker", {"force": True}
        )
    )
    assert result == api_dispatch.refuse_giw_force_lifecycle()
    settle.assert_not_called()


def test_rag_non_force_passes_settle_before_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def _run_gated(*_args, **kwargs):
        captured.update(kwargs)
        hook = kwargs.get("before_release")
        if hook is not None:
            await hook(RAG_MSG)
        return {"status": "ok", "message": RAG_MSG}

    monkeypatch.setattr(api_dispatch, "run_gated", _run_gated)
    monkeypatch.setattr(
        api_dispatch,
        "preempt_giw_keep_await_if_needed",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        api_dispatch,
        "preempt_stargate_idle_drain_if_needed",
        AsyncMock(return_value=None),
    )
    with patch.object(api_dispatch, "_pid_alive", return_value=True):
        with patch(
            "scripts.model_manager.ui.controller.service_ctl.startup_probe.uds_socket_live",
            return_value=True,
        ):
            ctl = MagicMock()
            _run(api_dispatch.execute(ctl, "sync_restart", "rag", {}))
    assert captured.get("before_release") is api_dispatch._rag_restart_settle_before_release


def test_parse_starting_pid_from_lifecycle_message() -> None:
    assert api_dispatch._parse_starting_pid("RAG service starting (PID 99).") == 99
    assert api_dispatch._parse_starting_pid("no pid here") is None
