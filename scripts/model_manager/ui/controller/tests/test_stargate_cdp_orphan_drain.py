"""AC1–AC2: non-operator CDP legs defer; operator/mission legs are named and do not hold."""

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
)
from scripts.model_manager.ui.controller.restart_intent_store import RestartIntentStore

pytestmark = pytest.mark.offline


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class _Probe:
    def __init__(self, detail: dict[str, Any]) -> None:
        self._detail = detail
        self.snapshots = 0

    async def snapshot(self) -> ActiveWork:
        self.snapshots += 1
        return ActiveWork(busy=bool(self._detail.get("busy")), detail=self._detail)


def test_non_operator_leg_defers_supervised_restart_under_ceiling(tmp_path: Any) -> None:
    detail = {
        "async_pipelines_running": 0,
        "requests_in_flight": 0,
        "cdp_non_operator_open": 1,
        "cdp_legs_orphaned": [
            {"execution_id": "exec-mission", "purpose": "mission"},
        ],
        "total": 1,
        "busy": True,
    }
    probe = _Probe(detail)
    store = RestartIntentStore(db_path=tmp_path / "defer.db")
    gate = RestartDrainGate(probes={"stargate": probe})
    calls = 0

    async def _lifecycle() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    supervisor = StargateIdleDrainSupervisor(
        gate=gate,
        store=store,
        lifecycle=_lifecycle,
        deadline_s=STARGATE_OPERATOR_IDLE_CEILING_S,
        poll_interval_s=0.01,
    )

    async def _arm() -> dict[str, Any]:
        result = await run_gated_stargate_idle_drain_supervised(
            gate,
            "sync_restart",
            store=store,
            supervisor=supervisor,
            reason="non-operator open",
        )
        await asyncio.sleep(0.05)
        return result

    result = _run(_arm())
    assert result["status"] == "deferred"
    assert result["idle_ceiling_s"] == 600
    assert calls == 0
    assert result["cdp_legs_orphaned"] == [
        {"execution_id": "exec-mission", "purpose": "mission"}
    ]


def test_operator_mission_leg_does_not_hold_and_is_named(tmp_path: Any) -> None:
    detail = {
        "async_pipelines_running": 0,
        "requests_in_flight": 0,
        "cdp_non_operator_open": 0,
        "cdp_legs_orphaned": [
            {"execution_id": "exec-proxy", "purpose": "operator-proxy"},
        ],
        "total": 0,
        "busy": False,
    }
    store = RestartIntentStore(db_path=tmp_path / "orphan.db")
    gate = RestartDrainGate(probes={"stargate": _Probe(detail)})
    calls = 0

    async def _lifecycle() -> str:
        nonlocal calls
        calls += 1
        return "restarted"

    supervisor = StargateIdleDrainSupervisor(
        gate=gate,
        store=store,
        lifecycle=_lifecycle,
        deadline_s=STARGATE_OPERATOR_IDLE_CEILING_S,
        poll_interval_s=0.01,
    )

    async def _arm() -> dict[str, Any]:
        result = await run_gated_stargate_idle_drain_supervised(
            gate,
            "sync_restart",
            store=store,
            supervisor=supervisor,
            reason="operator orphan",
        )
        await asyncio.sleep(0.05)
        return result

    result = _run(_arm())
    assert calls == 1
    assert result["cdp_legs_orphaned"] == [
        {"execution_id": "exec-proxy", "purpose": "operator-proxy"}
    ]
    assert result["idle_ceiling_s"] == STARGATE_OPERATOR_IDLE_CEILING_S
