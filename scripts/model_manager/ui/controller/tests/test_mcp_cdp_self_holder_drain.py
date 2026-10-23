"""MCP sync_restart drain intents: sole CDP self-holder and busy McpBusyProbe (a:39903).

Breaks when a seated operator stream (one restart-gate holder, caller id matches)
returns state=busy with no restart_intent_id, or when a second execution is
treated as that caller.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

import scripts.model_manager.ui.api_dispatch as api_dispatch
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


def _session_hot_detail() -> dict[str, Any]:
    return {
        "busy": True,
        "busy_reasons": ["mcp_session_hot"],
        "life_hot": True,
    }


class _StaticProbe:
    def __init__(self, work: ActiveWork) -> None:
        self._work = work

    async def snapshot(self) -> ActiveWork:
        return self._work


class _MutableProbe:
    def __init__(self, work: ActiveWork) -> None:
        self.work = work

    async def snapshot(self) -> ActiveWork:
        return self.work


class _RaisingProbe:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def snapshot(self) -> ActiveWork:
        raise self._exc


class _Ctl:
    def __init__(self, gate: RestartDrainGate, store: RestartIntentStore) -> None:
        self.restart_gate = gate
        self.restart_intent_store = store


async def _await_supervise_tasks() -> None:
    tasks = list(_SUPERVISE_TASKS)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _cancel_supervise_tasks() -> None:
    for task in list(_SUPERVISE_TASKS):
        task.cancel()
    await asyncio.gather(*list(_SUPERVISE_TASKS), return_exceptions=True)


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
        await _cancel_supervise_tasks()


@pytest.mark.asyncio
async def test_foreign_cdp_holder_arms_busy_drain_intent(
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
    try:
        assert result["status"] == "deferred"
        assert result["state"] == "draining"
        intent_id = result["restart_intent_id"]
        assert intent_id
        assert "Exit this CSE stream" not in result["guidance"]
        live = store.active_for_service("mcp")
        assert live is not None
        assert live.status == STATUS_PENDING_DRAIN
    finally:
        await _cancel_supervise_tasks()


@pytest.mark.asyncio
async def test_mcp_busy_non_force_arms_intent_and_restarts_on_idle(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle_calls: list[int] = []

    async def _lifecycle_spy(_ctl: Any, *, no_cache: bool) -> str:
        lifecycle_calls.append(1)
        return "restarted"

    monkeypatch.setattr(api_dispatch, "_mcp_deferred_lifecycle", _lifecycle_spy)

    store = RestartIntentStore(db_path=tmp_path / "mcp-busy-idle.db")
    probe = _MutableProbe(
        ActiveWork(busy=True, detail=_cdp_detail("other-exec")),
    )
    gate = RestartDrainGate(probes={"mcp": probe})
    ctl = _Ctl(gate, store)

    result = await _mcp_deferred_sync_restart(
        ctl,  # type: ignore[arg-type]
        None,
        method="sync_restart",
        force=False,
        no_cache=False,
    )
    assert result["status"] == "deferred"
    assert result["state"] == "draining"
    intent_id = result["restart_intent_id"]
    assert intent_id
    live = store.active_for_service("mcp")
    assert live is not None
    assert live.status == STATUS_PENDING_DRAIN
    assert live.reason == "manage sync_restart (mcp busy drain)"
    assert "Exit this CSE stream" not in result["guidance"]
    assert lifecycle_calls == []
    assert len(_SUPERVISE_TASKS) == 1

    probe.work = ActiveWork(busy=False, detail={"busy": False, "busy_reasons": []})
    assert lifecycle_calls == []
    await _await_supervise_tasks()

    assert lifecycle_calls == [1]
    final = store.get(intent_id)
    assert final is not None
    assert final.status == "completed"
    assert gate.restart_in_progress("mcp") is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("mcp down"),
        ValueError("bad payload"),
        OSError("mcp unreachable"),
    ],
)
async def test_mcp_probe_error_defers_without_intent(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    exc: BaseException,
) -> None:
    lifecycle_calls: list[int] = []

    async def _lifecycle_spy(_ctl: Any, *, no_cache: bool) -> str:
        lifecycle_calls.append(1)
        return "restarted"

    monkeypatch.setattr(api_dispatch, "_mcp_deferred_lifecycle", _lifecycle_spy)

    store = RestartIntentStore(
        db_path=tmp_path / f"mcp-probe-err-{type(exc).__name__}.db"
    )
    gate = RestartDrainGate(probes={"mcp": _RaisingProbe(exc)})
    ctl = _Ctl(gate, store)

    result = await _mcp_deferred_sync_restart(
        ctl,  # type: ignore[arg-type]
        None,
        method="sync_restart",
        force=False,
        no_cache=False,
    )
    assert result["status"] == "deferred"
    assert result["state"] == "probe_error"
    assert "restart_intent_id" not in result
    assert lifecycle_calls == []
    assert store.active_for_service("mcp") is None
    assert _SUPERVISE_TASKS == set()


@pytest.mark.asyncio
async def test_mcp_second_caller_while_drain_pending(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _hang(_ctl: Any, *, no_cache: bool) -> str:
        await asyncio.Event().wait()
        return ""

    monkeypatch.setattr(api_dispatch, "_mcp_deferred_lifecycle", _hang)

    store = RestartIntentStore(db_path=tmp_path / "mcp-second-caller.db")
    probe = _MutableProbe(
        ActiveWork(busy=True, detail=_cdp_detail("other-exec")),
    )
    gate = RestartDrainGate(probes={"mcp": probe})
    ctl = _Ctl(gate, store)

    first = await _mcp_deferred_sync_restart(
        ctl,  # type: ignore[arg-type]
        None,
        method="sync_restart",
        force=False,
        no_cache=False,
    )
    assert first["status"] == "deferred"
    assert first["state"] == "draining"
    intent_id = first["restart_intent_id"]
    assert intent_id

    second = await _mcp_deferred_sync_restart(
        ctl,  # type: ignore[arg-type]
        None,
        method="sync_restart",
        force=False,
        no_cache=False,
    )
    assert second["status"] == "deferred"
    assert second["restart_intent_id"] == intent_id
    assert second["reason"] == "drain already in progress for this service"
    assert len(_SUPERVISE_TASKS) == 1

    await _cancel_supervise_tasks()


@pytest.mark.asyncio
async def test_mcp_lifecycle_exception_after_idle(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _lifecycle_fail(_ctl: Any, *, no_cache: bool) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(api_dispatch, "_mcp_deferred_lifecycle", _lifecycle_fail)

    store = RestartIntentStore(db_path=tmp_path / "mcp-lifecycle-exc.db")
    probe = _MutableProbe(
        ActiveWork(busy=True, detail=_cdp_detail("other-exec")),
    )
    gate = RestartDrainGate(probes={"mcp": probe})
    ctl = _Ctl(gate, store)

    result = await _mcp_deferred_sync_restart(
        ctl,  # type: ignore[arg-type]
        None,
        method="sync_restart",
        force=False,
        no_cache=False,
    )
    intent_id = result["restart_intent_id"]
    assert intent_id

    probe.work = ActiveWork(busy=False, detail={"busy": False, "busy_reasons": []})
    await _await_supervise_tasks()

    final = store.get(intent_id)
    assert final is not None
    assert final.status == "failed"
    assert "lifecycle_exception" in (final.status_reason or "")
    assert gate.restart_in_progress("mcp") is False


@pytest.mark.asyncio
async def test_mcp_session_hot_arms_drain_without_cse_guidance(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle_calls: list[int] = []

    async def _lifecycle_spy(_ctl: Any, *, no_cache: bool) -> str:
        lifecycle_calls.append(1)
        return "restarted"

    monkeypatch.setattr(api_dispatch, "_mcp_deferred_lifecycle", _lifecycle_spy)

    store = RestartIntentStore(db_path=tmp_path / "mcp-session-hot.db")
    gate = RestartDrainGate(
        probes={
            "mcp": _StaticProbe(
                ActiveWork(busy=True, detail=_session_hot_detail()),
            )
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
    try:
        assert result["status"] == "deferred"
        assert result["state"] == "draining"
        assert result["restart_intent_id"]
        assert "Exit this CSE stream" not in result["guidance"]
        assert lifecycle_calls == []
    finally:
        await _cancel_supervise_tasks()
