"""Stargate operator idle-drain intents (agent-bus:12286 / inv 39)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from scripts.model_manager.ui.controller.restart_drain import (
    STARGATE_OPERATOR_IDLE_CEILING_S,
    ActiveWork,
    RestartDrainGate,
    StargateIdleDrainSupervisor,
    run_gated_stargate_idle_drain_supervised,
    stargate_idle_from_active_work,
)
from scripts.model_manager.ui.controller.restart_intent_store import (
    STATUS_COMPLETED,
    STATUS_FORCE_REQUESTED,
    STATUS_PENDING_DRAIN,
    RestartIntentStore,
)

pytestmark = pytest.mark.offline


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class _StargateBusyProbe:
    def __init__(self, *, busy: bool, requests_in_flight: int = 5) -> None:
        self._busy = busy
        self._requests_in_flight = requests_in_flight

    async def snapshot(self) -> ActiveWork:
        return ActiveWork(
            busy=self._busy,
            detail={
                "async_pipelines_running": 0,
                "requests_in_flight": self._requests_in_flight,
                "total": self._requests_in_flight,
                "busy": self._busy,
            },
        )


class _RecordingSupervisor:
    deadline_s = STARGATE_OPERATOR_IDLE_CEILING_S

    def __init__(self) -> None:
        self.intents: list[Any] = []
        self.lifecycle_calls = 0
        self._block = asyncio.Event()

    async def supervise(self, intent: Any) -> None:
        self.intents.append(intent)
        self.lifecycle_calls += 1
        await self._block.wait()


def test_stargate_idle_from_active_work_uses_requests_in_flight() -> None:
    assert not stargate_idle_from_active_work(
        {"busy": True, "requests_in_flight": 3, "async_pipelines_running": 0}
    )
    assert stargate_idle_from_active_work(
        {"busy": False, "requests_in_flight": 0, "async_pipelines_running": 0}
    )


def test_busy_stargate_sync_restart_mints_restart_intent(tmp_path: Any) -> None:
    store = RestartIntentStore(db_path=tmp_path / "stargate-intents.db")
    gate = RestartDrainGate(
        probes={"stargate": _StargateBusyProbe(busy=True, requests_in_flight=5)}
    )
    supervisor = _RecordingSupervisor()

    async def _arm() -> dict[str, Any]:
        result = await run_gated_stargate_idle_drain_supervised(
            gate,
            "sync_restart",
            store=store,
            supervisor=supervisor,
            reason="test stargate busy arm",
        )
        supervisor._block.set()
        await asyncio.sleep(0)
        return result

    result = _run(_arm())
    assert result["status"] == "deferred"
    assert result["state"] == "draining"
    assert result["restart_intent_id"]
    assert result["idle_ceiling_s"] == STARGATE_OPERATOR_IDLE_CEILING_S
    live = store.active_for_service("stargate")
    assert live is not None
    assert live.status == STATUS_PENDING_DRAIN
    assert len(supervisor.intents) == 1


def test_stargate_supervisor_ceiling_escalates_then_lifecycle(tmp_path: Any) -> None:
    store = RestartIntentStore(db_path=tmp_path / "stargate-ceiling.db")
    gate = RestartDrainGate(
        probes={"stargate": _StargateBusyProbe(busy=True, requests_in_flight=2)}
    )
    lifecycle_calls = 0

    async def _lifecycle() -> str:
        nonlocal lifecycle_calls
        lifecycle_calls += 1
        return "ok"

    supervisor = StargateIdleDrainSupervisor(
        gate=gate,
        store=store,
        lifecycle=_lifecycle,
        deadline_s=0.05,
        poll_interval_s=0.01,
    )
    intent = store.create_intent(
        service="stargate",
        action="sync_restart",
        deadline_at="d",
        reason="ceiling test",
    )

    statuses: list[str] = []
    original_cas = store.advance_if_status

    def _recording_cas(intent_id: str, **kwargs: Any) -> Any:
        status = kwargs.get("to_status")
        if isinstance(status, str):
            statuses.append(status)
        return original_cas(intent_id, **kwargs)

    store.advance_if_status = _recording_cas  # type: ignore[method-assign]

    _run(supervisor.supervise(intent))
    got = store.get(intent.intent_id)
    assert got is not None
    assert got.status == STATUS_COMPLETED
    assert lifecycle_calls == 1
    assert STATUS_FORCE_REQUESTED in statuses
