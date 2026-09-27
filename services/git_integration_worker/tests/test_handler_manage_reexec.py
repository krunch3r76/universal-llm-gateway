"""Propagate handler: manage uses guarded reexec; agent_bus census drops the caller."""

from __future__ import annotations

from typing import Any

import pytest
from implement_admission.propagation_row import PropagationRow

from scripts.model_manager.guarded_manage_reexec.result import GuardedReexecResult
from scripts.model_manager.ui.controller.charter_runner.propagation_execute import (
    ProbeDispatchResult,
)
from scripts.model_manager.ui.controller.restart_drain import (
    exclude_caller_from_agent_bus_census,
)
from services.git_integration_worker.cursor_auto.handler_propagation import (
    _execute_row,
)

pytestmark = pytest.mark.offline

_SHA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_SELF = "job-propagate-12286"
_PROBE = "scripts.model_manager.ui.controller.charter_runner.propagation_execute.dispatch_proof_probe"


def _row(service: str) -> PropagationRow:
    return PropagationRow(
        service=service,
        code_ref=_SHA,
        proof_class="process_live",
        proof_class_requested="process_live",
        proof=f"process_live probe for {service}",
    )


def _probe_ok(payload: dict[str, Any]) -> ProbeDispatchResult:
    return ProbeDispatchResult(
        payload=payload,
        proof_class_requested="process_live",
        proof_class_executed="process_live",
        error=None,
    )


def _self_only_payload() -> dict[str, Any]:
    return {
        "busy": True,
        "running": 0,
        "queued": 0,
        "cursor_dispatches": None,
        "write_lease": None,
        "active_count": 1,
        "active_ops": [
            {
                "kind": "cursor-auto",
                "op_id": _SELF,
                "contract": "propagate",
                "route": "cursor-auto/claimed",
            }
        ],
    }


@pytest.mark.asyncio
async def test_manage_row_calls_guarded_reexec_not_sync_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sync_calls: list[str] = []
    reexec_calls: list[dict[str, Any]] = []

    def _sync(*_args: Any, **_kwargs: Any) -> dict[str, str]:
        sync_calls.append("called")
        return {"status": "ok"}

    def _reexec(**kwargs: Any) -> GuardedReexecResult:
        reexec_calls.append(kwargs)
        return GuardedReexecResult(
            status="refused",
            reason="manage_inflight_or_activities",
            dry_run=bool(kwargs.get("dry_run")),
            executed=False,
            target_ref=kwargs.get("target_ref"),
        )

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler_propagation.sync_restart_service",
        _sync,
    )
    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.run_guarded_reexec",
        _reexec,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler_propagation.set_defer_reason",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        _PROBE, lambda _row: _probe_ok({"pid": 1, "code_version": _SHA})
    )

    out = await _execute_row(_row("manage"), row_id="manage-row", caller_job_id=_SELF)

    assert sync_calls == []
    assert reexec_calls == [{"target_ref": _SHA, "dry_run": False}]
    assert out["status"] == "failed"
    assert out["manage"]["reason"] == "manage_inflight_or_activities"
    assert out["manage"]["guarded_reexec"]["dry_run"] is False


@pytest.mark.asyncio
async def test_manage_proof_satisfied_closes_on_whoami_identity_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[str] = []
    probes = iter(
        (
            _probe_ok(
                {
                    "pid": 2089110,
                    "code_version": _SHA,
                    "process_start_time": "2026-09-27T07:34:05+00:00",
                }
            ),
            _probe_ok(
                {
                    "pid": 424242,
                    "code_version": _SHA,
                    "process_start_time": "2026-09-27T16:00:00+00:00",
                }
            ),
        )
    )

    def _reexec(**kwargs: Any) -> GuardedReexecResult:
        return GuardedReexecResult(
            status="proof-satisfied",
            reason="proof_satisfied",
            dry_run=bool(kwargs.get("dry_run")),
            executed=True,
            target_ref=kwargs.get("target_ref"),
            code_version_ok=True,
            process_start_later_ok=True,
        )

    def _sync(*_args: Any, **_kwargs: Any) -> dict[str, str]:
        raise AssertionError("sync_restart")

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler_propagation.sync_restart_service",
        _sync,
    )
    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.run_guarded_reexec",
        _reexec,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler_propagation.close_row",
        lambda row_id, **_k: closed.append(row_id),
    )
    monkeypatch.setattr(_PROBE, lambda _row: next(probes))

    out = await _execute_row(_row("manage"), row_id="manage-row")

    assert out["status"] == "executed"
    assert closed == ["manage-row"]
    assert out["proof"]["pid"] == 424242
    assert out["proof_before"]["pid"] == 2089110


@pytest.mark.asyncio
async def test_agent_bus_handler_caller_id_clears_self_only_census(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Handler forwards caller_job_id; that id is what the census exclusion drops."""
    captured: dict[str, Any] = {}

    def _sync(service: str, **kwargs: Any) -> dict[str, Any]:
        captured["service"] = service
        captured["caller_job_id"] = kwargs.get("caller_job_id")
        return {
            "status": "deferred",
            "state": "busy",
            "reason": "in-flight work",
            "restart_intent_id": "intent-self",
        }

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler_propagation.sync_restart_service",
        _sync,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler_propagation.set_defer_reason",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        _PROBE,
        lambda _row: _probe_ok({"pid": 1, "code_version": _SHA}),
    )

    out = await _execute_row(
        _row("agent_bus"),
        row_id="agent-bus-row",
        caller_job_id=_SELF,
    )

    assert out["status"] == "harvest_wanted"
    assert captured["service"] == "agent_bus"
    assert captured["caller_job_id"] == _SELF
    work = exclude_caller_from_agent_bus_census(
        _self_only_payload(),
        exclude_job_id=captured["caller_job_id"],
    )
    assert work.busy is False
    assert work.detail["active_ops"] == []
