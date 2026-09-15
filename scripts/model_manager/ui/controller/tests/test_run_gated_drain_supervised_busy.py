"""Busy-path durable drain for MCP git-worker restarts (todo:manage-busy-drain-restart).

Regression for friction 25989: non-force sync_restart while the worker is busy
must create a pending_drain intent (fleet busy-skip), not soft state=busy.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from scripts.model_manager.ui.controller.restart_drain import (
    ActiveWork,
    RestartDrainGate,
    run_gated,
    run_gated_drain_supervised,
    run_gated_self_holder_drain_supervised,
)
from scripts.model_manager.ui.controller.restart_intent_store import (
    STATUS_PENDING_DRAIN,
    RestartIntentStore,
)

pytestmark = pytest.mark.offline

_SERVICE = "git_integration_worker"
_AGENT_BUS = "agent_bus"
_SELF_DISPATCH = "auto-lane11411-self"
_FOREIGN_DISPATCH = "auto-lane11411-foreign"


def _self_holder_active_work(dispatch_id: str = _SELF_DISPATCH) -> dict[str, object]:
    return {
        "busy": True,
        "active_count": 1,
        "write_lease": {
            "holder_dispatch_id": dispatch_id,
            "queue_depth": 0,
        },
        "cursor_sdk_gate": {
            "active": 1,
            "limit": 1,
            "busy_status": {
                "active_holder": {"dispatch_id": dispatch_id},
                "queue_depth": 0,
            },
        },
    }


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class _StaticBusyProbe:
    def __init__(self, work: ActiveWork) -> None:
        self._work = work

    async def snapshot(self) -> ActiveWork:
        return self._work


class _RecordingSupervisor:
    """Records supervise calls; never completes (unit scope = arming only)."""

    deadline_s = 604800.0

    def __init__(self) -> None:
        self.intents: list[Any] = []
        self._block = asyncio.Event()

    async def supervise(self, intent: Any) -> None:
        self.intents.append(intent)
        await self._block.wait()


@pytest.fixture
def events_log(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    log: list[tuple[str, dict[str, Any]]] = []

    async def _fake_emit(signal: str, payload: dict[str, Any], **_kw: Any) -> None:
        log.append((signal, payload))

    monkeypatch.setattr("scripts.model_manager.observation_event._emit", _fake_emit)
    return log


def test_busy_sync_restart_arms_durable_drain(
    tmp_path: Any, events_log: list[tuple[str, dict[str, Any]]]
) -> None:
    """Busy probe must not soft-defer without a restart_intent_id."""
    store = RestartIntentStore(db_path=tmp_path / "restart-intents.db")
    gate = RestartDrainGate(
        probes={
            _SERVICE: _StaticBusyProbe(
                ActiveWork(
                    busy=True,
                    detail={
                        "busy": True,
                        "active_count": 1,
                        "cursor_sdk_gate": {"active": 1, "limit": 1},
                    },
                )
            )
        }
    )
    supervisor = _RecordingSupervisor()

    async def _arm() -> dict[str, Any]:
        result = await run_gated_drain_supervised(
            gate,
            "sync_restart",
            _SERVICE,
            store=store,
            supervisor=supervisor,
            reason="test busy arm",
        )
        # Unblock supervise so the restart-mutex slot releases in finally.
        supervisor._block.set()
        await asyncio.sleep(0)
        return result

    result = _run(_arm())
    assert result["status"] == "deferred"
    assert result["state"] == "draining"
    assert result["caller_must_exit_to_release_lease"] is True
    assert "exit" in result["guidance"].lower()
    intent_id = result["restart_intent_id"]
    assert intent_id
    live = store.active_for_service(_SERVICE)
    assert live is not None
    assert live.intent_id == intent_id
    assert live.status == STATUS_PENDING_DRAIN
    assert len(supervisor.intents) == 1


def test_self_holder_busy_mints_restart_intent(
    tmp_path: Any, events_log: list[tuple[str, dict[str, Any]]]
) -> None:
    """Sole self-holder busy must mint restart_intent_id (agent_bus path)."""
    store = RestartIntentStore(db_path=tmp_path / "restart-intents-self.db")
    gate = RestartDrainGate(
        probes={
            _AGENT_BUS: _StaticBusyProbe(
                ActiveWork(busy=True, detail=_self_holder_active_work())
            )
        }
    )
    supervisor = _RecordingSupervisor()

    async def _arm() -> dict[str, Any]:
        result = await run_gated_self_holder_drain_supervised(
            gate,
            "sync_restart",
            _AGENT_BUS,
            store=store,
            supervisor=supervisor,
            reason="test self-holder arm",
            caller_dispatch_id=_SELF_DISPATCH,
        )
        supervisor._block.set()
        await asyncio.sleep(0)
        return result

    result = _run(_arm())
    assert result["status"] == "deferred"
    assert result["state"] == "draining"
    intent_id = result["restart_intent_id"]
    assert intent_id
    assert "-" in intent_id
    live = store.active_for_service(_AGENT_BUS)
    assert live is not None
    assert live.intent_id == intent_id
    assert live.status == STATUS_PENDING_DRAIN
    assert len(supervisor.intents) == 1


def test_foreign_holder_busy_still_soft_defer_without_intent(tmp_path: Any) -> None:
    """Foreign holder busy must stay state=busy with no restart_intent_id."""
    store = RestartIntentStore(db_path=tmp_path / "restart-intents-foreign.db")
    gate = RestartDrainGate(
        probes={
            _AGENT_BUS: _StaticBusyProbe(
                ActiveWork(
                    busy=True,
                    detail=_self_holder_active_work(_FOREIGN_DISPATCH),
                )
            )
        }
    )
    supervisor = _RecordingSupervisor()

    async def _arm() -> dict[str, Any]:
        return await run_gated_self_holder_drain_supervised(
            gate,
            "sync_restart",
            _AGENT_BUS,
            store=store,
            supervisor=supervisor,
            reason="test foreign holder",
            caller_dispatch_id=_SELF_DISPATCH,
        )

    result = _run(_arm())
    assert result["status"] == "deferred"
    assert result["state"] == "busy"
    assert "restart_intent_id" not in result
    assert store.active_for_service(_AGENT_BUS) is None
    assert not supervisor.intents


def test_run_gated_foreign_holder_busy_without_caller_id(tmp_path: Any) -> None:
    """Generic run_gated busy path unchanged when caller_dispatch_id is absent."""
    gate = RestartDrainGate(
        probes={
            _AGENT_BUS: _StaticBusyProbe(
                ActiveWork(busy=True, detail=_self_holder_active_work())
            )
        }
    )

    async def _lifecycle() -> str:
        return "should not run"

    result = _run(
        run_gated(
            gate,
            "sync_restart",
            _AGENT_BUS,
            force=False,
            lifecycle=_lifecycle,
        )
    )
    assert result["status"] == "deferred"
    assert result["state"] == "busy"
    assert "restart_intent_id" not in result


def test_busy_soft_defer_path_gone_for_force_false_evaluate(
    tmp_path: Any,
) -> None:
    """Document contrast: evaluate(force=False) still defers busy (other services)."""
    gate = RestartDrainGate(
        probes={
            _SERVICE: _StaticBusyProbe(
                ActiveWork(busy=True, detail={"busy": True, "active_count": 1})
            )
        }
    )
    outcome = _run(gate.evaluate(_SERVICE, force=False))
    assert outcome is not None
    assert outcome.state == "busy"
    soft = outcome.to_result()
    assert soft["status"] == "deferred"
    assert soft["state"] == "busy"
    assert "restart_intent_id" not in soft
