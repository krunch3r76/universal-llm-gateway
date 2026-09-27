"""agent_bus drain census excludes the propagating cursor-auto job.

GIW ``/active-work`` ``busy`` is ``active_count > 0``, which includes the
claimed Auto job. ``running`` / ``queued`` / ``cursor_dispatches`` /
``write_lease`` can all be empty while that job still sets ``busy``.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from scripts.model_manager.ui.api_dispatch import execute
from scripts.model_manager.ui.controller.restart_drain import (
    ActiveWork,
    RestartDrainGate,
    agent_bus_restart_would_defer,
    run_gated,
)

pytestmark = pytest.mark.offline

_SELF = "job-propagate-12286"
_BRIDGE = "bridge-foreign"


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
                "thread_id": "12286",
            }
        ],
    }


def _foreign_bridge_payload() -> dict[str, Any]:
    payload = _self_only_payload()
    payload["active_count"] = 2
    payload["active_ops"] = [
        *payload["active_ops"],
        {
            "kind": "cursor_sdk",
            "op_id": _BRIDGE,
            "dispatch_id": _BRIDGE,
            "subject_preview": "foreign live bridge",
            "state": "running",
        },
    ]
    payload["live_bridges"] = [
        {
            "kind": "live_bridge",
            "dispatch_id": _BRIDGE,
            "subject_preview": "foreign live bridge",
        }
    ]
    return payload


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class _StaticBusyProbe:
    def __init__(self, work: ActiveWork) -> None:
        self._work = work

    async def snapshot(self) -> ActiveWork:
        return self._work


def test_self_propagate_job_does_not_defer() -> None:
    decision = agent_bus_restart_would_defer(_self_only_payload(), exclude_job_id=_SELF)
    assert decision["restart_would_defer"] is False
    assert decision["busy"] is False
    assert decision["deferral"] is None

    gate = RestartDrainGate(
        probes={
            "agent_bus": _StaticBusyProbe(
                ActiveWork(busy=True, detail=_self_only_payload())
            )
        }
    )
    outcome = _run(gate.evaluate("agent_bus", force=False, exclude_job_id=_SELF))
    assert outcome is None
    _run(gate.release("agent_bus"))


def test_foreign_live_bridge_defers_and_names_holder() -> None:
    decision = agent_bus_restart_would_defer(
        _foreign_bridge_payload(), exclude_job_id=_SELF
    )
    assert decision["restart_would_defer"] is True
    assert decision["busy"] is True
    deferral = decision["deferral"]
    assert deferral is not None
    assert _BRIDGE in deferral["reason"]
    assert "foreign live bridge" in deferral["reason"]
    assert deferral["active_work"]["holders"][0]["op_id"] == _BRIDGE

    gate = RestartDrainGate(
        probes={
            "agent_bus": _StaticBusyProbe(
                ActiveWork(busy=True, detail=_foreign_bridge_payload())
            )
        }
    )
    outcome = _run(gate.evaluate("agent_bus", force=False, exclude_job_id=_SELF))
    assert outcome is not None
    assert outcome.state == "busy"
    rendered = outcome.to_result()
    assert _BRIDGE in rendered["reason"]
    assert _SELF not in rendered["reason"]


def test_same_payload_without_exclusion_still_defers() -> None:
    """Raw GIW busy bit is unchanged when the caller job is not identified."""
    gate = RestartDrainGate(
        probes={
            "agent_bus": _StaticBusyProbe(
                ActiveWork(busy=True, detail=_self_only_payload())
            )
        }
    )
    outcome = _run(gate.evaluate("agent_bus", force=False))
    assert outcome is not None
    assert outcome.state == "busy"
    assert "holder=" not in outcome.reason


def test_run_gated_proceeds_when_self_is_sole_occupant() -> None:
    gate = RestartDrainGate(
        probes={
            "agent_bus": _StaticBusyProbe(
                ActiveWork(busy=True, detail=_self_only_payload())
            )
        }
    )
    calls: list[str] = []

    async def _lifecycle() -> str:
        calls.append("ran")
        return "restarted"

    result = _run(
        run_gated(
            gate,
            "sync_restart",
            "agent_bus",
            force=False,
            lifecycle=_lifecycle,
            exclude_job_id=_SELF,
        )
    )
    assert result["status"] == "ok"
    assert calls == ["ran"]


def test_execute_forwards_caller_job_id_only_for_agent_bus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def _run_gated(*args: Any, **kwargs: Any) -> dict[str, str]:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {"status": "ok"}

    monkeypatch.setattr("scripts.model_manager.ui.api_dispatch.run_gated", _run_gated)
    monkeypatch.setattr(
        "scripts.model_manager.ui.api_dispatch.preempt_giw_keep_await_if_needed",
        AsyncMock(return_value=None),
    )
    ctl = MagicMock()
    result = _run(
        execute(
            ctl,
            "sync_restart",
            "agent_bus",
            {"caller_job_id": _SELF, "service": "agent_bus"},
        )
    )
    assert result["status"] == "ok"
    assert captured["kwargs"]["exclude_job_id"] == _SELF

    captured.clear()
    _run(
        execute(
            ctl,
            "sync_restart",
            "event_service",
            {"caller_job_id": _SELF},
        )
    )
    assert captured["kwargs"]["exclude_job_id"] is None
