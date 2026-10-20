"""MCP sync_restart arms a drain intent for the sole CDP execution (friction 39848).

Breaks when a seated operator stream (one restart-gate holder, caller id matches)
returns state=busy with no restart_intent_id, or when a second execution is
treated as that caller.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from scripts.model_manager.ui.api_dispatch import _mcp_deferred_sync_restart
from scripts.model_manager.ui.controller.restart_drain import (
    _SUPERVISE_TASKS,
    ActiveWork,
    RestartDrainGate,
    sole_cdp_self_holder_matches,
)
from scripts.model_manager.ui.controller.restart_intent_store import (
    STATUS_PENDING_DRAIN,
    RestartIntentStore,
)

pytestmark = pytest.mark.offline

_CALLER = "52eb2c83c5824fc6933bcee8f7dbf60c"


def _cdp_detail(
    holder_id: str = _CALLER,
    *,
    extra_holder: str | None = None,
    drain_busy_reason: str = "in_flight_recorded",
    effective_count: int = 1,
    unseated: list[str] | None = None,
    registry_error: str | None = None,
) -> dict[str, Any]:
    holders: list[dict[str, str]] = [
        {"kind": "execution", "op_id": holder_id},
    ]
    if extra_holder is not None:
        holders.append({"kind": "execution", "op_id": extra_holder})
    return {
        "busy": True,
        "busy_reasons": ["cdp_ask_live"],
        "active_count": 1,
        "cdp_ask": {
            "busy": True,
            "drain_busy_reason": drain_busy_reason,
            "effective_count": effective_count,
            "holders": holders,
            "unseated_pending": unseated or [],
            "registry_error": registry_error,
            "running_count": 1,
        },
    }


class _StaticProbe:
    def __init__(self, work: ActiveWork) -> None:
        self._work = work

    async def snapshot(self) -> ActiveWork:
        return self._work


class _Ctl:
    def __init__(self, gate: RestartDrainGate, store: RestartIntentStore) -> None:
        self.restart_gate = gate
        self.restart_intent_store = store


@pytest.fixture
def events_log(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    log: list[tuple[str, dict[str, Any]]] = []

    async def _fake_emit(signal: str, payload: dict[str, Any], **_kw: Any) -> None:
        log.append((signal, payload))

    monkeypatch.setattr("scripts.model_manager.observation_event._emit", _fake_emit)
    return log


def test_sole_cdp_match_requires_one_holder() -> None:
    assert sole_cdp_self_holder_matches(_cdp_detail(), _CALLER) is True
    assert sole_cdp_self_holder_matches(_cdp_detail("other-exec"), _CALLER) is False
    assert (
        sole_cdp_self_holder_matches(
            _cdp_detail(extra_holder="other-exec", effective_count=2), _CALLER
        )
        is False
    )
    assert (
        sole_cdp_self_holder_matches(_cdp_detail(unseated=["other-exec"]), _CALLER)
        is False
    )
    assert (
        sole_cdp_self_holder_matches(
            _cdp_detail(registry_error="OSError: down"), _CALLER
        )
        is False
    )
    assert (
        sole_cdp_self_holder_matches(
            _cdp_detail(drain_busy_reason="unseated_pending"), _CALLER
        )
        is False
    )


@pytest.mark.asyncio
async def test_sole_cdp_caller_arms_restart_intent(
    tmp_path: Any,
    events_log: list[tuple[str, dict[str, Any]]],
) -> None:
    store = RestartIntentStore(db_path=tmp_path / "mcp-cdp-self.db")
    gate = RestartDrainGate(
        probes={"mcp": _StaticProbe(ActiveWork(busy=True, detail=_cdp_detail()))}
    )
    ctl = _Ctl(gate, store)
    result = await _mcp_deferred_sync_restart(
        ctl,  # type: ignore[arg-type]
        None,
        method="sync_restart",
        force=False,
        no_cache=False,
        force_context={"caller_dispatch_id": _CALLER},
    )
    try:
        assert result["status"] == "deferred"
        assert result["state"] == "draining"
        intent_id = result["restart_intent_id"]
        assert intent_id
        assert "Exit this CSE stream" in result["guidance"]
        live = store.active_for_service("mcp")
        assert live is not None
        assert live.intent_id == intent_id
        assert live.status == STATUS_PENDING_DRAIN
    finally:
        for task in list(_SUPERVISE_TASKS):
            task.cancel()
        await asyncio.gather(*list(_SUPERVISE_TASKS), return_exceptions=True)


@pytest.mark.asyncio
async def test_foreign_cdp_holder_stays_busy_without_intent(
    tmp_path: Any,
    events_log: list[tuple[str, dict[str, Any]]],
) -> None:
    store = RestartIntentStore(db_path=tmp_path / "mcp-cdp-foreign.db")
    gate = RestartDrainGate(
        probes={
            "mcp": _StaticProbe(ActiveWork(busy=True, detail=_cdp_detail("other-exec")))
        }
    )
    ctl = _Ctl(gate, store)
    result = await _mcp_deferred_sync_restart(
        ctl,  # type: ignore[arg-type]
        None,
        method="sync_restart",
        force=False,
        no_cache=False,
        force_context={"caller_dispatch_id": _CALLER},
    )
    assert result["status"] == "deferred"
    assert result["state"] == "busy"
    assert "restart_intent_id" not in result
    assert store.active_for_service("mcp") is None
    assert _SUPERVISE_TASKS == set()
