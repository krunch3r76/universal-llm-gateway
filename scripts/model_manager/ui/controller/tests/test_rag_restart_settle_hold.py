"""RAG sync_restart settle hold — friction a:39394 plan r2, a:39455 start paths."""

from __future__ import annotations

import asyncio
import os
import time
from types import SimpleNamespace
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

    with patch.object(api_dispatch, "_rag_pid_considered_alive", return_value=True):
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

    with patch.object(api_dispatch, "_rag_pid_considered_alive", return_value=True):
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

    with patch.object(api_dispatch, "_rag_pid_considered_alive", return_value=False):
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


def test_rag_force_sync_restart_passes_settle_before_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def _run_gated(*_args, **kwargs):
        captured.update(kwargs)
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
    ctl = MagicMock()
    ctl.restart_gate = _gate()
    result = _run(api_dispatch.execute(ctl, "sync_restart", "rag", {"force": True}))
    assert result["status"] == "ok"
    assert (
        captured.get("before_release")
        is api_dispatch._rag_restart_settle_before_release
    )


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
    ctl.restart_gate = _gate()
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
    with patch.object(api_dispatch, "_rag_pid_considered_alive", return_value=True):
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


def _ctl_with_gate() -> MagicMock:
    gate = RestartDrainGate(probes={})
    ctl = MagicMock()
    ctl.restart_gate = gate
    return ctl


def test_rag_sync_restart_e2e_second_execute_in_progress_during_settle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R1: execute → real run_gated → real settle; second call during hold."""
    lifecycle_calls = 0
    lifecycle_returned = asyncio.Event()
    allow_ready = asyncio.Event()

    async def _sync_restart(_ctl, _service: str) -> str:
        nonlocal lifecycle_calls
        lifecycle_calls += 1
        lifecycle_returned.set()
        return RAG_MSG

    async def _passthrough_lifecycle(_ctl, _service, _action, inner):
        return await inner()

    def _uds_live(_path: object) -> bool:
        return allow_ready.is_set()

    monkeypatch.setattr(api_dispatch, "_sync_restart", _sync_restart)
    monkeypatch.setattr(
        api_dispatch, "_lifecycle_with_restart_window", _passthrough_lifecycle
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

    with patch.object(api_dispatch, "_rag_pid_considered_alive", return_value=True):
        with patch(
            "scripts.model_manager.ui.controller.service_ctl.startup_probe.uds_socket_live",
            _uds_live,
        ):
            with patch(
                "scripts.model_manager.ui.controller.service_config.read_rag_tcp_config",
                return_value=None,
            ):
                ctl = _ctl_with_gate()

                async def _exercise() -> tuple[dict[str, Any], dict[str, Any]]:
                    first = asyncio.create_task(
                        api_dispatch.execute(ctl, "sync_restart", "rag", {})
                    )
                    await lifecycle_returned.wait()
                    second = await api_dispatch.execute(
                        ctl, "sync_restart", "rag", {}
                    )
                    allow_ready.set()
                    first_result = await first
                    return first_result, second

                first_result, second = _run(_exercise())

    assert lifecycle_calls == 1
    assert first_result["status"] == "ok"
    assert second["status"] == "deferred"
    assert second["state"] == "in_progress"
    assert "4242" in second["reason"]


def _arm_settle_not_ready(monkeypatch: pytest.MonkeyPatch) -> asyncio.Event:
    """Pid stays alive and transport stays down until the returned event is set."""
    allow_ready = asyncio.Event()

    def _uds_live(_path: object) -> bool:
        return allow_ready.is_set()

    def _pid_alive(_pid: int) -> bool:
        return True

    async def _passthrough(_ctl: object, _service: str, _action: str, inner):
        return await inner()

    async def _finalize(_ctl: object, _service: str, result: dict[str, Any], **_kwargs):
        return result

    monkeypatch.setattr(api_dispatch, "RAG_RESTART_SETTLE_BUDGET_S", 0.3)
    monkeypatch.setattr(api_dispatch, "_rag_pid_considered_alive", _pid_alive)
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.service_config.read_rag_tcp_config",
        lambda: None,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.service_ctl.startup_probe.uds_socket_live",
        _uds_live,
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
    monkeypatch.setattr(api_dispatch, "_lifecycle_with_restart_window", _passthrough)
    monkeypatch.setattr(api_dispatch, "_finalize_restart_rebuild", _finalize)
    return allow_ready


@pytest.mark.parametrize(
    ("method", "params", "lifecycle_name"),
    [
        ("start", {}, "_start"),
        ("restart", {}, "_restart_cycle"),
        ("restart", {"force": True}, "_restart_cycle"),
        ("sync_restart", {"force": True}, "_sync_restart"),
        ("charter", {}, "_sync_restart"),
    ],
    ids=["start", "restart", "restart-force", "sync-restart-force", "charter-harvest"],
)
def test_rag_start_path_blocks_nonforce_sync_restart_during_settle(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    params: dict[str, Any],
    lifecycle_name: str,
) -> None:
    """A non-force sync_restart during the hold must not run stop (SIGKILL)."""
    allow_ready = _arm_settle_not_ready(monkeypatch)
    counts = {"lifecycle": 0, "second_sync": 0}
    entered = asyncio.Event()

    async def _lifecycle(*_args, **_kwargs) -> str:
        counts["lifecycle"] += 1
        entered.set()
        return RAG_MSG

    async def _second_sync(*_args, **_kwargs) -> str:
        counts["second_sync"] += 1
        return RAG_MSG

    monkeypatch.setattr(api_dispatch, lifecycle_name, _lifecycle)
    if lifecycle_name != "_sync_restart":
        monkeypatch.setattr(api_dispatch, "_sync_restart", _second_sync)
    if method == "charter":
        monkeypatch.setattr(
            "scripts.model_manager.ui.controller.service_ctl.authority_identity"
            ".snapshot_before_restart",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(api_dispatch, "_wait_healthy", AsyncMock(return_value=0.0))

    ctl = _ctl_with_gate()

    async def _exercise() -> tuple[dict[str, Any], dict[str, Any]]:
        if method == "charter":
            first = asyncio.create_task(
                api_dispatch.sync_restart_charter_harvest(ctl, "rag")
            )
        else:
            first = asyncio.create_task(
                api_dispatch.execute(ctl, method, "rag", params)
            )
        await entered.wait()
        second = await api_dispatch.execute(ctl, "sync_restart", "rag", {})
        allow_ready.set()
        return await first, second

    first_result, second = _run(_exercise())

    assert counts["lifecycle"] == 1
    assert counts["second_sync"] == 0
    assert first_result["status"] == "ok"
    assert second["status"] == "deferred"
    assert second["state"] == "in_progress"
    assert "4242" in second["reason"]


def test_tui_start_rag_blocks_nonforce_sync_restart_during_settle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TUI rag start opens the same hold; a later non-force sync_restart waits."""
    from scripts.model_manager.ui.view.screens.services import ServicesScreen

    allow_ready = _arm_settle_not_ready(monkeypatch)
    counts = {"lifecycle": 0, "second_sync": 0}
    entered = asyncio.Event()

    async def _second_sync(*_args, **_kwargs) -> str:
        counts["second_sync"] += 1
        return RAG_MSG

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(api_dispatch, "_sync_restart", _second_sync)
    monkeypatch.setattr(
        "scripts.model_manager.ui.view.screens.services.asyncio.sleep",
        _no_sleep,
    )

    ctl = _ctl_with_gate()

    async def _start_rag() -> str:
        counts["lifecycle"] += 1
        entered.set()
        return RAG_MSG

    ctl.start_rag = _start_rag

    class _Tui:
        _force_armed = False
        _force_reason = ""
        _run_gated_action = ServicesScreen._run_gated_action

        def __init__(self) -> None:
            self.app = SimpleNamespace(service_controller=ctl, event_bus=None)

        def query_one(self, *_args: object, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(write_line=lambda _line: None)

        def _consume_force(self) -> None:
            return None

        def _refresh_status(self) -> None:
            return None

        async def _tui_lifecycle_with_window(
            self,
            _svc: object,
            _service: str,
            _action: str,
            lifecycle,
        ) -> str:
            return await lifecycle()

    screen = _Tui()

    async def _exercise() -> dict[str, Any]:
        first = asyncio.create_task(ServicesScreen._start_rag(screen))
        await entered.wait()
        second = await api_dispatch.execute(ctl, "sync_restart", "rag", {})
        allow_ready.set()
        await first
        return second

    second = _run(_exercise())

    assert counts["lifecycle"] == 1
    assert counts["second_sync"] == 0
    assert second["status"] == "deferred"
    assert second["state"] == "in_progress"
    assert "4242" in second["reason"]


def test_rag_settle_strict_budget_with_slow_transport_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R2: executor probe must not extend past the 45s (here 0.15s) budget."""
    monkeypatch.setattr(api_dispatch, "RAG_RESTART_SETTLE_BUDGET_S", 0.15)

    async def _slow_probe() -> bool:
        await asyncio.sleep(1.0)
        return False

    monkeypatch.setattr(api_dispatch, "_probe_rag_transport_ready", _slow_probe)
    with patch.object(api_dispatch, "_rag_pid_considered_alive", return_value=True):
        t0 = time.monotonic()
        _run(api_dispatch._rag_restart_settle_before_release(RAG_MSG))
        elapsed = time.monotonic() - t0
    assert elapsed < 0.45


def test_rag_zombie_pid_releases_settle_hold_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R3: zombie child must not keep the hold until the full budget."""
    monkeypatch.setattr(api_dispatch, "RAG_RESTART_SETTLE_BUDGET_S", 45.0)

    with patch.object(os, "kill", return_value=None):
        with patch.object(os, "waitpid", return_value=(4242, 0)):
            with patch(
                "scripts.model_manager.ui.controller.service_ctl.startup_probe.uds_socket_live",
                return_value=False,
            ):
                t0 = time.monotonic()
                _run(api_dispatch._rag_restart_settle_before_release(RAG_MSG))
                elapsed = time.monotonic() - t0
    assert elapsed < 0.5


def test_rag_settle_probes_tcp_when_tcp_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R4: TCP rag config uses a TCP connect probe, not the UDS path."""
    tcp_calls: list[tuple[str, int]] = []

    def _tcp_live(host: str, port: int) -> bool:
        tcp_calls.append((host, port))
        return True

    monkeypatch.setattr(api_dispatch, "_tcp_socket_live", _tcp_live)
    with patch.object(api_dispatch, "_rag_pid_considered_alive", return_value=True):
        with patch(
            "scripts.model_manager.ui.controller.service_config.read_rag_tcp_config",
            return_value=("127.0.0.1", 8765),
        ):
            with patch(
                "scripts.model_manager.ui.controller.service_ctl.startup_probe.uds_socket_live",
                side_effect=AssertionError("UDS probe must not run in TCP mode"),
            ):
                _run(api_dispatch._rag_restart_settle_before_release(RAG_MSG))
    assert tcp_calls == [("127.0.0.1", 8765)]


def test_rag_settle_probes_uds_when_not_tcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uds_calls: list[object] = []

    def _uds_live(path: object) -> bool:
        uds_calls.append(path)
        return True

    with patch.object(api_dispatch, "_rag_pid_considered_alive", return_value=True):
        with patch(
            "scripts.model_manager.ui.controller.service_config.read_rag_tcp_config",
            return_value=None,
        ):
            with patch(
                "scripts.model_manager.ui.controller.service_ctl.startup_probe.uds_socket_live",
                _uds_live,
            ):
                _run(api_dispatch._rag_restart_settle_before_release(RAG_MSG))
    assert len(uds_calls) == 1


def test_fleet_rag_start_respects_settle_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fleet sync/rebuild must not start rag while the settle slot is held."""
    from pathlib import Path

    from scripts.model_manager.ui.controller import fleet_local

    monkeypatch.setattr(fleet_local, "is_rag_configured", lambda: True)
    for name in (
        "is_agent_bus_configured",
        "is_cloud_proxy_configured",
        "is_cortex_configured",
        "is_email_bridge_configured",
        "is_cdp_ask_manage_enabled",
    ):
        monkeypatch.setattr(fleet_local, name, lambda: False)
    monkeypatch.setattr(fleet_local, "is_mcp_configured", lambda _root: False)
    monkeypatch.setattr(fleet_local, "FLEET_SKIP_GIT_WORKER", True)

    ctl = _ctl_with_gate()
    calls = {"start": 0}

    async def _start_rag() -> str:
        calls["start"] += 1
        return RAG_MSG

    ctl.start_rag = _start_rag
    rag_op = next(
        op
        for name, op in fleet_local._build_start_ops(
            ctl, Path("."), rebuild_supporting_services=False
        )
        if name == "rag"
    )
    hold_entered = asyncio.Event()
    release_hold = asyncio.Event()

    async def before_release(_message: str) -> None:
        hold_entered.set()
        await release_hold.wait()

    async def _exercise() -> str:
        first = asyncio.create_task(
            _run_gated_with_hook(ctl.restart_gate, before_release)
        )
        await hold_entered.wait()
        try:
            return await rag_op()
        finally:
            release_hold.set()
            await first

    message = _run(_exercise())
    assert calls["start"] == 0
    assert "in progress" in message
