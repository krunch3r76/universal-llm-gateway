"""Stargate operator idle-drain intents (agent-bus:12286 / inv 39)."""

from __future__ import annotations

import asyncio
import errno
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from scripts.model_manager.ui import api_dispatch
from scripts.model_manager.ui.controller import stargate_inflight_witness as sg_witness
from scripts.model_manager.ui.controller.drain_timeout_keep_await import (
    preempt_stargate_idle_drain_if_needed,
    register_supervise_task,
)
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
    STATUS_DRAINED_RESTARTING,
    STATUS_FORCE_REQUESTED,
    STATUS_PENDING_DRAIN,
    RestartIntentStore,
)
from scripts.model_manager.ui.controller.stargate_inflight_witness import (
    StargateDownWitness,
)
from scripts.model_manager.ui.controller.tests.conftest import stub_process_live_probe

pytestmark = pytest.mark.offline


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _record_stargate_health_get(
    monkeypatch: pytest.MonkeyPatch,
) -> list[int]:
    """Stub Stargate /health port probe; return the call log."""
    ports: list[int] = []

    def _port_open(port: int, host: str = "127.0.0.1") -> bool:
        ports.append(port)
        return False

    from scripts.model_manager.ui.model.service_state import ServiceState

    monkeypatch.setattr(ServiceState, "_port_open", staticmethod(_port_open))
    return ports


class _StargateBusyProbe:
    def __init__(self, *, busy: bool, requests_in_flight: int = 5) -> None:
        self._busy = busy
        self._requests_in_flight = requests_in_flight
        self.snapshot_calls = 0

    async def snapshot(self) -> ActiveWork:
        self.snapshot_calls += 1
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


def test_busy_stargate_sync_restart_mints_restart_intent(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe_live_calls = stub_process_live_probe(monkeypatch)
    health_ports = _record_stargate_health_get(monkeypatch)
    store = RestartIntentStore(db_path=tmp_path / "stargate-intents.db")
    stargate_probe = _StargateBusyProbe(busy=True, requests_in_flight=5)
    gate = RestartDrainGate(probes={"stargate": stargate_probe})
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
    assert stargate_probe.snapshot_calls >= 1
    assert health_ports == []
    assert "stargate" in probe_live_calls


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


def _connect_refused_error() -> httpx.ConnectError:
    request = httpx.Request("GET", "http://127.0.0.1:9999/api/v1/admin/active-work")
    exc = httpx.ConnectError("Connection refused", request=request)
    exc.__cause__ = OSError(errno.ECONNREFUSED, "Connection refused")
    return exc


class _RaisingProbe:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc
        self.snapshot_calls = 0

    async def snapshot(self) -> ActiveWork:
        self.snapshot_calls += 1
        raise self._exc


def test_stargate_econnrefused_and_down_witness_starts(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RestartIntentStore(db_path=tmp_path / "stargate-down-start.db")
    lifecycle_calls = 0

    async def _lifecycle() -> str:
        nonlocal lifecycle_calls
        lifecycle_calls += 1
        return "started"

    gate = RestartDrainGate(
        probes={"stargate": _RaisingProbe(_connect_refused_error())}
    )
    supervisor = StargateIdleDrainSupervisor(
        gate=gate,
        store=store,
        lifecycle=_lifecycle,
    )
    monkeypatch.setattr(sg_witness, "stargate_down_witness_agrees", lambda: True)

    result = _run(
        run_gated_stargate_idle_drain_supervised(
            gate,
            "sync_restart",
            store=store,
            supervisor=supervisor,
            reason="down stargate immediate start",
        )
    )
    assert result["status"] == "ok"
    assert lifecycle_calls == 1
    assert store.active_for_service("stargate") is None


def test_stargate_connect_error_with_live_pid_stays_on_ceiling(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe_live_calls = stub_process_live_probe(monkeypatch)
    health_ports = _record_stargate_health_get(monkeypatch)
    store = RestartIntentStore(db_path=tmp_path / "stargate-live-pid.db")
    lifecycle_calls = 0

    async def _lifecycle() -> str:
        nonlocal lifecycle_calls
        lifecycle_calls += 1
        return "nope"

    raising = _RaisingProbe(_connect_refused_error())
    gate = RestartDrainGate(probes={"stargate": raising})
    supervisor = StargateIdleDrainSupervisor(
        gate=gate,
        store=store,
        lifecycle=_lifecycle,
        deadline_s=0.05,
        poll_interval_s=0.01,
    )
    monkeypatch.setattr(sg_witness, "stargate_down_witness_agrees", lambda: False)

    async def _arm() -> dict[str, Any]:
        result = await run_gated_stargate_idle_drain_supervised(
            gate,
            "sync_restart",
            store=store,
            supervisor=supervisor,
            reason="live pid connect refused",
        )
        await asyncio.sleep(0)
        return result

    result = _run(_arm())
    assert result["status"] == "deferred"
    live = store.active_for_service("stargate")
    assert live is not None
    assert live.status == STATUS_PENDING_DRAIN
    assert lifecycle_calls == 0
    assert raising.snapshot_calls >= 1
    assert health_ports == []
    assert "stargate" in probe_live_calls


def test_stargate_read_timeout_stays_on_ceiling(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe_live_calls = stub_process_live_probe(monkeypatch)
    health_ports = _record_stargate_health_get(monkeypatch)
    store = RestartIntentStore(db_path=tmp_path / "stargate-timeout.db")
    lifecycle_calls = 0

    async def _lifecycle() -> str:
        nonlocal lifecycle_calls
        lifecycle_calls += 1
        return "nope"

    request = httpx.Request("GET", "http://127.0.0.1:9999/api/v1/admin/active-work")
    raising = _RaisingProbe(httpx.ReadTimeout("slow", request=request))
    gate = RestartDrainGate(probes={"stargate": raising})
    supervisor = StargateIdleDrainSupervisor(
        gate=gate,
        store=store,
        lifecycle=_lifecycle,
    )

    async def _arm() -> dict[str, Any]:
        result = await run_gated_stargate_idle_drain_supervised(
            gate,
            "sync_restart",
            store=store,
            supervisor=supervisor,
            reason="read timeout",
        )
        await asyncio.sleep(0)
        return result

    result = _run(_arm())
    assert result["status"] == "deferred"
    live = store.active_for_service("stargate")
    assert live is not None
    assert live.status == STATUS_PENDING_DRAIN
    assert lifecycle_calls == 0
    assert raising.snapshot_calls >= 1
    assert health_ports == []
    assert "stargate" in probe_live_calls


def test_stargate_force_preempts_idle_drain_slot(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RestartIntentStore(db_path=tmp_path / "stargate-force-preempt.db")
    gate = RestartDrainGate(probes={"stargate": _StargateBusyProbe(busy=True)})
    intent = store.create_intent(
        service="stargate",
        action="sync_restart",
        deadline_at="d",
        reason="hold slot",
    )
    assert intent.status == STATUS_PENDING_DRAIN
    hold = asyncio.Event()

    async def _supervise(_intent: Any) -> None:
        await hold.wait()

    async def _preempt() -> Any:
        task = asyncio.create_task(_supervise(intent))
        register_supervise_task("stargate", task)
        ctl = MagicMock(restart_intent_store=store, restart_gate=gate)
        result = await preempt_stargate_idle_drain_if_needed(ctl, "stargate", True)
        hold.set()
        await asyncio.sleep(0)
        return result

    assert _run(_preempt()) is None
    assert not gate.restart_in_progress("stargate")
    assert store.active_for_service("stargate") is None
    got = store.get(intent.intent_id)
    assert got is not None
    assert got.status == STATUS_FORCE_REQUESTED


def test_refused_caller_force_leaves_pending_drain(tmp_path: Any) -> None:
    """A refused caller force does not CAS pending_drain or cancel supervise.

    Breaks when preempt runs before the gate: the intent becomes
    force_requested and the supervise task is cancelled, then the gate
    returns 409.
    """
    store = RestartIntentStore(db_path=tmp_path / "stargate-refuse-preempt.db")
    gate = RestartDrainGate(probes={"stargate": _StargateBusyProbe(busy=True)})
    intent = store.create_intent(
        service="stargate",
        action="restart",
        deadline_at="d",
        reason="hold slot",
    )
    assert intent.status == STATUS_PENDING_DRAIN
    cancelled = False

    async def _supervise(_intent: Any) -> None:
        nonlocal cancelled
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled = True
            raise

    async def _exercise() -> dict[str, Any]:
        task = asyncio.create_task(_supervise(intent))
        register_supervise_task("stargate", task)
        ctl = MagicMock(restart_intent_store=store, restart_gate=gate)
        try:
            result = await api_dispatch.execute(
                ctl, "restart", "stargate", {"force": True}
            )
            assert result["status"] == "refused"
            assert result["status_code"] == 409
            live = store.get(intent.intent_id)
            assert live is not None
            assert live.status == STATUS_PENDING_DRAIN
            assert cancelled is False
            assert task.cancelled() is False
            return result
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    _run(_exercise())


def test_stargate_force_lost_cas_does_not_cancel(
    tmp_path: Any,
) -> None:
    store = RestartIntentStore(db_path=tmp_path / "stargate-force-wait.db")
    gate = RestartDrainGate(probes={"stargate": _StargateBusyProbe(busy=True)})
    intent = store.create_intent(
        service="stargate",
        action="sync_restart",
        deadline_at="d",
        reason="drained restarting",
    )
    store.advance_if_status(
        intent.intent_id,
        from_status=STATUS_PENDING_DRAIN,
        to_status=STATUS_DRAINED_RESTARTING,
        reason="test",
    )
    cancelled = False

    done_flag: asyncio.Task[None] | None = None

    async def _supervise(_intent: Any) -> None:
        nonlocal cancelled
        try:
            await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            cancelled = True
            raise

    async def _preempt() -> Any:
        nonlocal done_flag
        task = asyncio.create_task(_supervise(intent))
        done_flag = task
        register_supervise_task("stargate", task)
        ctl = MagicMock(restart_intent_store=store, restart_gate=gate)
        return await preempt_stargate_idle_drain_if_needed(ctl, "stargate", True)

    assert _run(_preempt()) is None
    assert cancelled is False
    assert done_flag is not None and done_flag.done()


def test_stargate_evaluate_read_timeout_fails_closed() -> None:
    request = httpx.Request(
        "GET", "http://127.0.0.1:9999/api/v1/admin/active-work"
    )
    gate = RestartDrainGate(
        probes={
            "stargate": _RaisingProbe(httpx.ReadTimeout("slow", request=request))
        }
    )
    outcome = _run(gate.evaluate("stargate", force=False))
    assert outcome is not None
    assert outcome.state == "probe_error"
    assert gate.restart_in_progress("stargate") is False


def test_stargate_busy_report_timeout_undetermined_with_down_witness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = httpx.Request(
        "GET", "http://127.0.0.1:9999/api/v1/admin/active-work"
    )
    gate = RestartDrainGate(
        probes={
            "stargate": _RaisingProbe(httpx.ReadTimeout("slow", request=request))
        },
        witnesses={"stargate": [StargateDownWitness()]},
    )
    monkeypatch.setattr(sg_witness, "stargate_down_witness_agrees", lambda: False)
    report = _run(gate.busy_report(["stargate"]))
    row = report["stargate"]
    assert row["determination"] == "undetermined"
    assert row["restart_would_defer"] is True


def test_giw_force_still_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        api_dispatch,
        "preempt_stargate_idle_drain_if_needed",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        api_dispatch,
        "run_gated",
        AsyncMock(side_effect=AssertionError("run_gated must not run")),
    )
    ctl = MagicMock()
    result = _run(
        api_dispatch.execute(
            ctl,
            "sync_restart",
            "git_integration_worker",
            {"force": True},
        )
    )
    assert result == api_dispatch.refuse_giw_force_lifecycle()
