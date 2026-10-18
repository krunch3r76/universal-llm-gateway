"""Gate 1 force_reason requirement for manage lifecycle (friction:39668)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from scripts.model_manager.ui.controller.restart_drain import (
    ActiveWork,
    RestartDrainGate,
    run_gated,
    run_gated_deferred,
)
from scripts.model_manager.ui.controller.restart_drain_force_gate import (
    REFUSE_REASON,
    enforce_force_reason_if_needed,
    force_exempt_services,
    force_reason_accepted,
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
        )
    )
    assert result is None
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
        )
    )
    assert posted == [""]


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
        )

    result = _run(_run_it())
    assert result["status"] == "refused"
    lifecycle.assert_not_called()


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
        )

    result = _run(_run_it())
    assert result["status"] == "refused"
    lifecycle.assert_not_called()


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
