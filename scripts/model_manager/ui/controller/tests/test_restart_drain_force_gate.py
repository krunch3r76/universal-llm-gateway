"""Gate 1 force_reason requirement for manage lifecycle (friction:39668)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from unittest.mock import AsyncMock

import pytest

from scripts.model_manager.ui.controller.git_worker_drain_supervisor import (
    GitWorkerDrainSupervisor,
)
from scripts.model_manager.ui.controller.restart_drain import (
    ActiveWork,
    RestartDrainGate,
    StargateIdleDrainSupervisor,
    run_gated,
    run_gated_deferred,
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
        posted.append(
            {"thread_id": thread_id or "", "subject": subject, "body": body}
        )

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


def test_idle_force_proceeds_without_reason(events_log: list[tuple[str, dict[str, Any]]]) -> None:
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
    assert any(
        "no dispatching lane thread" in rec.message for rec in caplog.records
    )


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
