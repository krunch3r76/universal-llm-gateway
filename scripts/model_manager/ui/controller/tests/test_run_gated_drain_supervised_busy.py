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
    DrainOutcome,
    RestartDrainGate,
    arm_agent_bus_busy_drain,
    run_gated,
    run_gated_drain_supervised,
    run_gated_self_holder_drain_supervised,
)
from scripts.model_manager.ui.controller.restart_intent_store import (
    STATUS_PENDING_DRAIN,
    RestartIntentStore,
)
from scripts.model_manager.ui.controller.tests.conftest import stub_process_live_probe

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
        self.snapshot_calls = 0

    async def snapshot(self) -> ActiveWork:
        self.snapshot_calls += 1
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
    tmp_path: Any,
    events_log: list[tuple[str, dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Busy probe must not soft-defer without a restart_intent_id."""
    probe_live_calls = stub_process_live_probe(monkeypatch)
    live_http: list[str] = []

    def _forbid_async_client(*_a: object, **_k: object) -> None:
        live_http.append("make_async_client")
        raise AssertionError("live HttpActiveWorkProbe must not run under guard")

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.restart_drain.make_async_client",
        _forbid_async_client,
    )
    store = RestartIntentStore(db_path=tmp_path / "restart-intents.db")
    giw_probe = _StaticBusyProbe(
        ActiveWork(
            busy=True,
            detail={
                "busy": True,
                "active_count": 1,
                "cursor_sdk_gate": {"active": 1, "limit": 1},
            },
        )
    )
    gate = RestartDrainGate(probes={_SERVICE: giw_probe})
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
    assert live_http == []
    assert _SERVICE in probe_live_calls


def test_self_holder_busy_mints_restart_intent(
    tmp_path: Any,
    events_log: list[tuple[str, dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sole self-holder busy must mint restart_intent_id (agent_bus path)."""
    probe_live_calls = stub_process_live_probe(monkeypatch)
    live_http: list[str] = []

    def _forbid_async_client(*_a: object, **_k: object) -> None:
        live_http.append("make_async_client")
        raise AssertionError("live agent_bus probe must not run under guard")

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.restart_drain.make_async_client",
        _forbid_async_client,
    )
    store = RestartIntentStore(db_path=tmp_path / "restart-intents-self.db")
    bus_probe = _StaticBusyProbe(
        ActiveWork(busy=True, detail=_self_holder_active_work())
    )
    gate = RestartDrainGate(probes={_AGENT_BUS: bus_probe})
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
    assert bus_probe.snapshot_calls >= 1
    assert live_http == []
    assert _AGENT_BUS in probe_live_calls


def test_deferred_body_counts_cse_holders_instead_of_listing_them() -> None:
    """A deferred manage body must not carry the cse_holders row list."""
    rows = [{"holder_id": f"cse_{i}", "state": "driving"} for i in range(3)]
    outcome = DrainOutcome(
        state="busy",
        service="agent_bus",
        reason="in flight",
        active_work={"busy": True, "cse_holders": rows, "active_count": 1},
    )
    rendered = outcome.to_result()
    assert "cse_holders" not in rendered["active_work"]
    assert rendered["active_work"]["cse_holder_count"] == 3
    assert rendered["active_work"]["active_count"] == 1
    assert outcome.active_work["cse_holders"] == rows


def test_agent_bus_foreign_busy_mints_restart_intent(
    tmp_path: Any,
    events_log: list[tuple[str, dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Foreign lane-B busy must arm restart_intent_id, not soft-defer."""
    stub_process_live_probe(monkeypatch)
    store = RestartIntentStore(db_path=tmp_path / "restart-intents-agent-bus.db")
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

    async def _arm() -> dict[str, Any] | None:
        result = await arm_agent_bus_busy_drain(
            gate,
            store=store,
            supervisor=supervisor,
            reason="test foreign agent_bus arm",
        )
        supervisor._block.set()
        await asyncio.sleep(0)
        return result

    result = _run(_arm())
    assert result is not None
    assert result["status"] == "deferred"
    assert result["state"] == "draining"
    assert result["restart_intent_id"]
    assert store.active_for_service(_AGENT_BUS) is not None
    assert len(supervisor.intents) == 1


def test_agent_bus_idle_after_caller_exclusion_does_not_arm(tmp_path: Any) -> None:
    """The propagating job alone is idle and must not mint an intent."""
    store = RestartIntentStore(db_path=tmp_path / "restart-intents-self-job.db")
    detail = {
        "busy": True,
        "active_count": 1,
        "active_ops": [{"kind": "cursor-auto", "op_id": _SELF_DISPATCH}],
    }
    gate = RestartDrainGate(
        probes={_AGENT_BUS: _StaticBusyProbe(ActiveWork(busy=True, detail=detail))}
    )
    supervisor = _RecordingSupervisor()
    result = _run(
        arm_agent_bus_busy_drain(
            gate,
            store=store,
            supervisor=supervisor,
            reason="test self job",
            exclude_job_id=_SELF_DISPATCH,
        )
    )
    assert result is None
    assert store.active_for_service(_AGENT_BUS) is None
    assert not supervisor.intents


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


class _DownProbe:
    async def snapshot(self) -> ActiveWork:
        raise OSError("giw down")


def test_agent_bus_probe_error_falls_through_to_run_gated() -> None:
    """GIW down must defer via run_gated, not escape the arm as JSON-RPC -32000."""
    gate = RestartDrainGate(probes={_AGENT_BUS: _DownProbe()})

    armed = _run(
        arm_agent_bus_busy_drain(
            gate,
            store=None,
            supervisor=None,
            reason="probe down",
        )
    )
    assert armed is None

    async def _lifecycle() -> str:
        raise AssertionError("must not restart on a failed probe")

    deferred = _run(
        run_gated(
            gate,
            "sync_restart",
            _AGENT_BUS,
            force=False,
            lifecycle=_lifecycle,
        )
    )
    assert deferred["status"] == "deferred"
    assert deferred["state"] == "probe_error"


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
