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
    run_gated_drain_supervised,
)
from scripts.model_manager.ui.controller.restart_intent_store import (
    STATUS_PENDING_DRAIN,
    RestartIntentStore,
)

pytestmark = pytest.mark.offline

_SERVICE = "git_integration_worker"


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

    monkeypatch.setattr(
        "scripts.model_manager.observation_event._emit", _fake_emit
    )
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
