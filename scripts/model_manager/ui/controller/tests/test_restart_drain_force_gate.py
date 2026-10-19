"""Gate 1 force_reason requirement for manage lifecycle (friction:39668)."""

from __future__ import annotations

import asyncio
import errno
import json
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from scripts.model_manager.ui.controller.git_worker_drain_supervisor import (
    GitWorkerDrainSupervisor,
)
from scripts.model_manager.ui.controller.restart_drain import (
    ActiveWork,
    DrainOutcome,
    RestartDrainGate,
    StargateIdleDrainSupervisor,
    resume_drain_supervision,
    run_gated,
    run_gated_deferred,
    run_gated_drain_supervised_blocking,
    run_gated_stargate_idle_drain_supervised,
)
from scripts.model_manager.ui.controller.restart_drain_force_gate import (
    REFUSE_REASON,
    enforce_force_reason_if_needed,
    force_exempt_services,
    force_reason_accepted,
)
from scripts.model_manager.ui.controller.restart_intent_store import (
    STATUS_FORCE_REQUESTED,
    RestartIntentStore,
)

pytestmark = pytest.mark.offline


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class _StaticProbe:
    def __init__(self, work: ActiveWork) -> None:
        self._work = work

    async def snapshot(self) -> ActiveWork:
        return self._work


def _busy_gate() -> RestartDrainGate:
    gate = RestartDrainGate()
    gate._probes["mcp"] = _StaticProbe(  # noqa: SLF001
        ActiveWork(busy=True, detail={"busy": True, "active_ops": [{"op_id": "job-1"}]})
    )
    return gate


def _idle_gate() -> RestartDrainGate:
    gate = RestartDrainGate()
    gate._probes["mcp"] = _StaticProbe(ActiveWork(busy=False))  # noqa: SLF001
    return gate


@pytest.fixture
def events_log(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    log: list[tuple[str, dict[str, Any]]] = []

    async def _fake_emit(signal: str, payload: dict[str, Any], **_kw: Any) -> None:
        log.append((signal, payload))

    monkeypatch.setattr("scripts.model_manager.observation_event._emit", _fake_emit)
    return log


@pytest.fixture
def notices(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    posted: list[dict[str, str]] = []

    def _schedule(*, thread_id: str | None, subject: str, body: str) -> None:
        posted.append({"thread_id": thread_id or "", "subject": subject, "body": body})

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.restart_drain_force_gate.schedule_force_gate_notice",
        _schedule,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.restart_drain_force_notice.schedule_force_gate_notice",
        _schedule,
    )
    return posted


def test_force_reason_accepted_rejects_blank() -> None:
    assert force_reason_accepted("ok") is True
    assert force_reason_accepted("  x  ") is True
    assert force_reason_accepted("") is False
    assert force_reason_accepted("   ") is False
    assert force_reason_accepted(None) is False


def test_exemption_list_empty_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MANAGE_FORCE_EXEMPT_SERVICES", raising=False)
    assert force_exempt_services() == frozenset()


def test_busy_force_no_reason_refused(notices: list[dict[str, str]]) -> None:
    gate = _busy_gate()
    result = _run(
        enforce_force_reason_if_needed(
            gate,
            "sync_restart",
            "mcp",
            force=True,
            force_reason=None,
            caller_thread_id="18180",
            caller_requested=True,
        )
    )
    assert result is not None
    assert result["status"] == "refused"
    assert result["reason"] == REFUSE_REASON
    assert result["restart_would_defer"] is True
    assert notices and notices[0]["thread_id"] == "18180"


def test_busy_force_empty_string_reason_refused() -> None:
    gate = _busy_gate()
    result = _run(
        enforce_force_reason_if_needed(
            gate,
            "sync_restart",
            "mcp",
            force=True,
            force_reason="   ",
            caller_requested=True,
        )
    )
    assert result is not None
    assert result["reason"] == REFUSE_REASON


def test_busy_force_with_reason_proceeds_and_audits(
    events_log: list[tuple[str, dict[str, Any]]],
    notices: list[dict[str, str]],
) -> None:
    gate = _busy_gate()
    result = _run(
        enforce_force_reason_if_needed(
            gate,
            "sync_restart",
            "mcp",
            force=True,
            force_reason="coordinator: clear stale holder",
            caller_thread_id="18180",
            caller_dispatch_id="disp-1",
            caller_requested=True,
        )
    )
    assert result is None
    applied = [p for s, p in events_log if s == "manage.restart.force_applied"]
    assert len(applied) == 1
    assert applied[0]["force_reason"].startswith("coordinator:")
    assert notices and "applied" in notices[0]["body"]


def test_idle_force_proceeds_without_reason(
    events_log: list[tuple[str, dict[str, Any]]],
) -> None:
    gate = _idle_gate()
    result = _run(
        enforce_force_reason_if_needed(
            gate,
            "sync_restart",
            "mcp",
            force=True,
            force_reason=None,
            caller_requested=True,
        )
    )
    assert result is None
    assert not [s for s, _ in events_log if s == "manage.restart.force_applied"]


def test_internal_force_true_skips_gate_without_caller_requested(
    events_log: list[tuple[str, dict[str, Any]]],
) -> None:
    """Internal run_gated(force=True) must not hit Gate 1 (busy + no reason)."""
    gate = _busy_gate()
    lifecycle = AsyncMock(return_value="done")

    async def _run_it() -> dict[str, Any]:
        return await run_gated(
            gate,
            "sync_restart",
            "mcp",
            force=True,
            lifecycle=lifecycle,
        )

    result = _run(_run_it())
    assert result["status"] == "ok"
    lifecycle.assert_awaited_once()
    assert not [s for s, _ in events_log if s == "manage.restart.force_applied"]


def test_exempt_service_forces_without_reason(
    monkeypatch: pytest.MonkeyPatch,
    events_log: list[tuple[str, dict[str, Any]]],
) -> None:
    monkeypatch.setenv("MANAGE_FORCE_EXEMPT_SERVICES", "cdp_ask")
    gate = RestartDrainGate()
    gate._probes["cdp_ask"] = _StaticProbe(  # noqa: SLF001
        ActiveWork(busy=True, detail={"busy": True})
    )
    result = _run(
        enforce_force_reason_if_needed(
            gate,
            "sync_restart",
            "cdp_ask",
            force=True,
            force_reason=None,
            caller_requested=True,
        )
    )
    assert result is None
    assert not [s for s, _ in events_log if s == "manage.restart.force_applied"]


def test_giw_force_not_handled_here() -> None:
    gate = _busy_gate()
    result = _run(
        enforce_force_reason_if_needed(
            gate,
            "sync_restart",
            "git_integration_worker",
            force=True,
            force_reason=None,
            caller_requested=True,
        )
    )
    assert result is None


def test_probe_error_treated_as_busy(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = RestartDrainGate()

    async def _report(_services: Any) -> dict[str, dict[str, Any]]:
        return {
            "mcp": {
                "busy": False,
                "restart_would_defer": True,
                "determination": "undetermined",
                "active_work": {"error": "TimeoutError"},
            }
        }

    monkeypatch.setattr(gate, "busy_report", _report)
    result = _run(
        enforce_force_reason_if_needed(
            gate,
            "stop",
            "mcp",
            force=True,
            force_reason=None,
            caller_requested=True,
        )
    )
    assert result is not None
    assert result["reason"] == REFUSE_REASON


def test_notice_path_when_no_lane_known(monkeypatch: pytest.MonkeyPatch) -> None:
    posted: list[str] = []

    def _schedule(*, thread_id: str | None, subject: str, body: str) -> None:
        posted.append(thread_id or "")

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.restart_drain_force_gate.schedule_force_gate_notice",
        _schedule,
    )
    gate = _busy_gate()
    _run(
        enforce_force_reason_if_needed(
            gate,
            "sync_restart",
            "mcp",
            force=True,
            force_reason=None,
            caller_requested=True,
        )
    )
    assert posted == [""]


def test_notice_skipped_logs_when_no_lane(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(
        logging.WARNING,
        logger="scripts.model_manager.ui.controller.restart_drain_force_notice",
    )
    gate = _busy_gate()
    _run(
        enforce_force_reason_if_needed(
            gate,
            "sync_restart",
            "mcp",
            force=True,
            force_reason=None,
            caller_requested=True,
        )
    )
    assert any("no dispatching lane thread" in rec.message for rec in caplog.records)


def test_run_gated_busy_force_refused_skips_lifecycle() -> None:
    gate = _busy_gate()
    lifecycle = AsyncMock(side_effect=AssertionError("must not run"))

    async def _run_it() -> dict[str, Any]:
        return await run_gated(
            gate,
            "sync_restart",
            "mcp",
            force=True,
            lifecycle=lifecycle,
            caller_requested=True,
        )

    result = _run(_run_it())
    assert result["status"] == "refused"
    lifecycle.assert_not_called()


def test_force_refused_is_409_names_force_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = _busy_gate()
    lifecycle = AsyncMock(side_effect=AssertionError("must not run"))
    evaluate = AsyncMock(side_effect=AssertionError("gate.evaluate must not run"))
    monkeypatch.setattr(gate, "evaluate", evaluate)

    async def _run_it() -> dict[str, Any]:
        return await run_gated(
            gate,
            "sync_restart",
            "mcp",
            force=True,
            lifecycle=lifecycle,
            caller_requested=True,
        )

    result = _run(_run_it())
    assert result["status"] == "refused"
    assert result["status_code"] == 409
    assert "force_reason" in json.dumps(result)
    lifecycle.assert_not_called()
    evaluate.assert_not_called()


def test_run_gated_busy_force_with_reason_runs_lifecycle() -> None:
    gate = _busy_gate()
    lifecycle = AsyncMock(return_value="done")

    async def _run_it() -> dict[str, Any]:
        return await run_gated(
            gate,
            "sync_restart",
            "mcp",
            force=True,
            force_reason="test: proceed",
            lifecycle=lifecycle,
            caller_requested=True,
        )

    result = _run(_run_it())
    assert result["status"] == "ok"
    lifecycle.assert_awaited_once()


def test_run_gated_deferred_refused_before_background() -> None:
    gate = _busy_gate()
    lifecycle = AsyncMock(side_effect=AssertionError("must not run"))

    async def _run_it() -> dict[str, Any]:
        return await run_gated_deferred(
            gate,
            "sync_restart",
            "mcp",
            force=True,
            lifecycle=lifecycle,
            scheduled_message="scheduled",
            caller_requested=True,
        )

    result = _run(_run_it())
    assert result["status"] == "refused"
    lifecycle.assert_not_called()


class _StargateBusyProbe:
    def __init__(self) -> None:
        self.snapshot_calls = 0

    async def snapshot(self) -> ActiveWork:
        self.snapshot_calls += 1
        return ActiveWork(
            busy=True,
            detail={
                "requests_in_flight": 3,
                "async_pipelines_running": 0,
                "busy": True,
            },
        )


def test_idle_ceiling_self_preempt_not_gated(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RestartIntentStore(db_path=tmp_path / "ceiling-gate.db")
    gate = RestartDrainGate(probes={"stargate": _StargateBusyProbe()})
    lifecycle_calls = 0

    async def _lifecycle() -> str:
        nonlocal lifecycle_calls
        lifecycle_calls += 1
        return "ok"

    async def _enforce_fail(*_a: Any, **_k: Any) -> None:
        raise AssertionError("enforce_force_reason_if_needed must not run")

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.restart_drain_force_gate.enforce_force_reason_if_needed",
        _enforce_fail,
    )

    supervisor = StargateIdleDrainSupervisor(
        gate=gate,
        store=store,
        lifecycle=_lifecycle,
        deadline_s=0.0,
        poll_interval_s=0.01,
    )
    intent = store.create_intent(
        service="stargate",
        action="sync_restart",
        deadline_at="d",
        reason="ceiling gate test",
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
    assert lifecycle_calls == 1
    assert STATUS_FORCE_REQUESTED in statuses


def test_giw_park_live_not_gated(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RestartIntentStore(db_path=tmp_path / "park-live-gate.db")
    intent = store.create_intent(
        service="git_integration_worker",
        action="restart",
        deadline_at="d",
        reason="park gate test",
        park_live=True,
    )
    sweep_ran = False
    summary = {"requested": [], "refused": [], "live_after": 0}

    async def _enforce_fail(*_a: Any, **_k: Any) -> None:
        raise AssertionError("enforce_force_reason_if_needed must not run")

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.restart_drain_force_gate.enforce_force_reason_if_needed",
        _enforce_fail,
    )

    class _Kill:
        async def __call__(self) -> str:
            return "stopped"

    sup = GitWorkerDrainSupervisor(
        store=store,
        begin_drain=AsyncMock(return_value={}),
        drain_state=AsyncMock(return_value={}),
        subscribe_events=AsyncMock(),
        kill=_Kill(),
    )

    async def _sweep(_self: Any, _intent: Any, *, reason: str) -> dict[str, Any]:
        nonlocal sweep_ran
        sweep_ran = True
        return summary

    monkeypatch.setattr(GitWorkerDrainSupervisor, "_run_park_sweep", _sweep)

    async def _emit(**_kw: Any) -> None:
        return None

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.git_worker_drain_supervisor.events.emit_manage_restart_park_live_requested",
        _emit,
    )

    _run(sup._park_live(intent))
    assert sweep_ran is True


def test_resolve_notice_thread_id_prefers_caller() -> None:
    from scripts.model_manager.ui.controller.restart_drain_force_notice import (
        resolve_notice_thread_id,
    )

    assert (
        resolve_notice_thread_id(
            caller_thread_id="18180",
            caller_dispatch_id="d1",
            holder={"thread_id": "9999"},
        )
        == "18180"
    )


def test_resolve_notice_thread_id_falls_back_to_holder() -> None:
    from scripts.model_manager.ui.controller.restart_drain_force_notice import (
        resolve_notice_thread_id,
    )

    assert (
        resolve_notice_thread_id(
            caller_thread_id=None,
            caller_dispatch_id=None,
            holder={"thread_id": "9999"},
        )
        == "9999"
    )


def test_stargate_health_auto_restart_not_caller_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Health-loop force restart skips Gate 1 while stargate is busy.

    Breaks when ``_force_restart_stargate`` passes ``caller_requested``: a
    busy probe with no ``force_reason`` returns 409 and the lifecycle does
    not run.
    """
    gate = RestartDrainGate()
    gate._probes["stargate"] = _StaticProbe(  # noqa: SLF001
        ActiveWork(busy=True, detail={"busy": True, "requests_in_flight": 4})
    )
    controller = MagicMock()
    controller.restart_gate = gate

    async def _window(*_args: Any, **_kwargs: Any) -> str:
        return "restarted"

    monkeypatch.setattr(
        "scripts.model_manager.ui.api_dispatch._lifecycle_with_restart_window",
        _window,
    )
    from scripts.model_manager.ui.controller.stargate_health_restart import (
        _force_restart_stargate,
    )

    result = _run(_force_restart_stargate(controller))
    assert result["status"] == "ok"
    assert result["message"] == "restarted"
    assert result.get("reason") != REFUSE_REASON


def test_stargate_econnrefused_force_stays_caller_requested_false(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Down-stargate immediate start calls ``run_gated(force=True)`` unguarded.

    Breaks when that call sets ``caller_requested``: Gate 1 runs on an
    internal skip of the idle-drain arm.
    """
    import httpx

    request = httpx.Request("GET", "http://127.0.0.1:9999/api/v1/admin/active-work")
    exc = httpx.ConnectError("Connection refused", request=request)
    exc.__cause__ = OSError(errno.ECONNREFUSED, "Connection refused")

    class _RaisingProbe:
        async def snapshot(self) -> ActiveWork:
            raise exc

    store = RestartIntentStore(db_path=tmp_path / "econn-force-gate.db")
    gate = RestartDrainGate(probes={"stargate": _RaisingProbe()})
    seen: list[bool] = []

    async def _record(*_args: Any, **kwargs: Any) -> None:
        seen.append(bool(kwargs.get("caller_requested")))
        return None

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.restart_drain_force_gate.enforce_force_reason_if_needed",
        _record,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.stargate_inflight_witness.stargate_down_witness_agrees",
        lambda: True,
    )

    async def _lifecycle() -> str:
        return "started"

    supervisor = StargateIdleDrainSupervisor(
        gate=gate,
        store=store,
        lifecycle=_lifecycle,
    )
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
    assert seen == [False]


def test_tui_force_arm_does_not_set_caller_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TUI Force toggle sets ``force`` and leaves ``caller_requested`` false.

    Breaks when the services screen forwards the arm as a manage force
    request: a busy service with no reason field would 409.
    """
    captured: dict[str, Any] = {}

    async def _run_gated(*_args: Any, **kwargs: Any) -> dict[str, str]:
        captured.update(kwargs)
        return {"status": "ok", "message": "stopped"}

    monkeypatch.setattr(
        "scripts.model_manager.ui.view.screens.services.run_gated",
        _run_gated,
    )
    from scripts.model_manager.ui.view.screens.services import ServicesScreen

    async def _window(*_args: Any, **_kwargs: Any) -> str:
        return "stopped"

    screen = SimpleNamespace(
        _force_armed=True,
        app=SimpleNamespace(service_controller=SimpleNamespace(restart_gate=object())),
        query_one=lambda *_a, **_k: SimpleNamespace(write_line=lambda _m: None),
        _consume_force=lambda: None,
        _tui_lifecycle_with_window=_window,
    )
    result = _run(
        ServicesScreen._run_gated_action(
            screen,  # type: ignore[arg-type]
            "stop",
            "mcp",
            AsyncMock(return_value="stopped"),
        )
    )
    assert result is True
    assert captured["force"] is True
    assert captured.get("caller_requested", False) is False


def test_manage_sock_force_forward_is_caller_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operator-proxy ``force=True`` is a manage request, so Gate 1 binds.

    Breaks when ``sync_restart_service`` drops the flag or
    ``manage_force_context`` stops treating params force as caller-requested:
    a busy mcp/cdp_ask force would skip the reason requirement.
    """
    from scripts.model_manager.ui.api_dispatch import manage_force_context
    from services.git_integration_worker.relay.manage_sock import sync_restart_service

    captured: dict[str, Any] = {}

    def _call(
        method: str, params: dict[str, Any] | None = None, *, timeout: float = 0.0
    ) -> dict[str, str]:
        captured["method"] = method
        captured["params"] = params or {}
        return {"status": "ok"}

    monkeypatch.setattr(
        "services.git_integration_worker.relay.manage_sock.call_manage",
        _call,
    )
    sync_restart_service("cdp_ask", force=True)
    assert captured["method"] == "sync_restart"
    assert captured["params"]["force"] is True
    assert manage_force_context(captured["params"])["caller_requested"] is True


def test_supervised_evaluate_force_does_not_enter_reason_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Blocking fleet drain and intent resume take the slot with force.

    They call ``gate.evaluate(force=True, supervised_drain=True)`` and must
    not enter ``enforce_force_reason_if_needed``. Breaks when either path is
    routed through the caller-requested reason gate.
    """
    calls: list[tuple[str, bool, bool]] = []

    class _Gate:
        async def evaluate(
            self,
            service: str,
            *,
            force: bool,
            supervised_drain: bool = False,
            exclude_job_id: str | None = None,
        ) -> DrainOutcome:
            del exclude_job_id
            calls.append((service, force, supervised_drain))
            return DrainOutcome(state="busy", service=service, reason="held")

    async def _enforce(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("enforce_force_reason_if_needed must not run")

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.restart_drain_force_gate.enforce_force_reason_if_needed",
        _enforce,
    )
    store = MagicMock()
    store.active_for_service.return_value = None
    supervisor = SimpleNamespace(deadline_s=1.0)
    result = _run(
        run_gated_drain_supervised_blocking(
            _Gate(),  # type: ignore[arg-type]
            "restart",
            "git_integration_worker",
            store=store,
            supervisor=supervisor,
            reason="fleet drain",
        )
    )
    assert result["status"] == "deferred"
    assert calls == [("git_integration_worker", True, True)]
    calls.clear()
    _run(
        resume_drain_supervision(
            _Gate(),  # type: ignore[arg-type]
            "stargate",
            supervisor=supervisor,
            intent=object(),
        )
    )
    assert calls == [("stargate", True, True)]
