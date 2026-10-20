"""Boot reconcile picks drain supervisors by intent.service (not always GIW)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.model_manager.ui.controller.restart_drain import (
    ActiveWork,
    RestartDrainGate,
    _SUPERVISE_TASKS,
)
from scripts.model_manager.ui.controller.restart_intent_store import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    RestartIntentStore,
)
from scripts.model_manager.ui.controller.service_ctl.restart_reconcile import (
    reconcile_pending_restart_intents,
)

pytestmark = pytest.mark.offline


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class _IdleProbe:
    async def snapshot(self) -> ActiveWork:
        return ActiveWork(busy=False, detail={"busy": False})


def test_reconcile_routes_agent_bus_mcp_and_marks_unsupported_failed(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = RestartIntentStore(db_path=tmp_path / "restart-intents-reconcile.db")
    agent_bus_intent = store.create_intent(
        service="agent_bus", action="sync_restart", deadline_at="d", reason="ab"
    )
    mcp_intent = store.create_intent(
        service="mcp", action="sync_restart", deadline_at="d", reason="mcp"
    )
    rag_intent = store.create_intent(
        service="rag", action="sync_restart", deadline_at="d", reason="rag"
    )

    giw_build_calls: list[dict[str, Any]] = []

    def _forbid_giw_build(**kwargs: Any) -> object:
        giw_build_calls.append(kwargs)
        raise AssertionError("build_git_worker_drain_supervisor must not run")

    agent_bus_stops: list[str] = []
    agent_bus_starts: list[str] = []
    mcp_restarts: list[bool] = []

    async def stop_agent_bus() -> str:
        agent_bus_stops.append("stop")
        return "agent_bus stopped"

    async def start_agent_bus() -> str:
        agent_bus_starts.append("start")
        return "agent_bus started"

    async def sync_restart_mcp(*, no_cache: bool = False) -> str:
        mcp_restarts.append(no_cache)
        return "mcp restarted"

    gate = RestartDrainGate(
        probes={"agent_bus": _IdleProbe(), "mcp": _IdleProbe()}
    )

    controller = SimpleNamespace(
        _restart_intent_store=store,
        restart_intent_store=store,
        _restart_gate=gate,
        build_git_worker_drain_supervisor=_forbid_giw_build,
        git_worker_kill_for=lambda _action: "kill",
        stop_agent_bus=stop_agent_bus,
        start_agent_bus=start_agent_bus,
        sync_restart_mcp=sync_restart_mcp,
    )

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.service_ctl.restart_reconcile.reconcile_pending_validations_at_boot",
        lambda **_kw: None,
    )

    async def _fake_emit(*_a: object, **_k: object) -> None:
        return None

    monkeypatch.setattr("scripts.model_manager.observation_event._emit", _fake_emit)

    async def _noop_settle(*_a: object, **_k: object) -> None:
        return None

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.restart_window_ctl.invoke_propagation_settle_for_service",
        _noop_settle,
    )

    async def _noop_boot_render_diff() -> str:
        return ""

    monkeypatch.setattr(
        "scripts.model_manager.ui.api_dispatch._run_boot_render_diff",
        _noop_boot_render_diff,
    )

    async def _reconcile_and_await() -> None:
        await reconcile_pending_restart_intents(controller)
        pending = list(_SUPERVISE_TASKS)
        if pending:
            await asyncio.gather(*pending)

    _run(_reconcile_and_await())

    assert giw_build_calls == []
    assert agent_bus_stops == ["stop"]
    assert agent_bus_starts == ["start"]
    assert mcp_restarts == [False]

    rag = store.get(rag_intent.intent_id)
    assert rag is not None
    assert rag.status == STATUS_FAILED
    fail_reason = rag.status_reason or ""
    assert "unsupported_service:rag" in fail_reason

    ab = store.get(agent_bus_intent.intent_id)
    mcp = store.get(mcp_intent.intent_id)
    assert ab is not None and ab.status == STATUS_COMPLETED
    assert mcp is not None and mcp.status == STATUS_COMPLETED
