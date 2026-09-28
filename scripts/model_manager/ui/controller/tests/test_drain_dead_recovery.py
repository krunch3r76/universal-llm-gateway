"""Dead-target drain recovery: start, don't keep-await a corpse."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.model_manager.ui.controller.drain_dead_recovery import (
    paired_stop_then_start,
)
from scripts.model_manager.ui.controller.git_worker_drain_supervisor import (
    GitWorkerDrainSupervisor,
)
from scripts.model_manager.ui.controller.giw_recycle import recycle_giw
from scripts.model_manager.ui.controller.restart_intent_states import (
    STATUS_PENDING_DRAIN,
    STATUS_VERIFYING_ACTIVATION,
)
from scripts.model_manager.ui.controller.restart_intent_store import RestartIntentStore
from scripts.model_manager.ui.model.service_state import ServiceStatus

_SERVICE = "git_integration_worker"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _store(tmp_path: Any) -> RestartIntentStore:
    return RestartIntentStore(db_path=tmp_path / "restart-intents.db")


def _snap(**overrides: Any) -> dict[str, Any]:
    base = {
        "draining": True,
        "drain_epoch": 1,
        "intent_id": "intent",
        "worker_id": "w1",
        "pid": 100,
        "worker_started_at": "t1",
        "active_count": 1,
        "active_ops": [{"op_id": "job"}],
        "deadline_at": None,
    }
    base.update(overrides)
    return base


class _Kill:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self) -> str:
        self.calls += 1
        return "stopped"


class _Start:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self) -> str:
        self.calls += 1
        return "started"


class _Feed:
    def __call__(self, resume_seq: int) -> Any:
        return self._gen()

    async def _gen(self) -> Any:
        if False:
            yield {}


def _patch_validate(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _arm(store: RestartIntentStore, intent: Any) -> bool:
        store.advance(intent.intent_id, status=STATUS_VERIFYING_ACTIVATION)
        return True

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.drain_dead_recovery.arm_verify_after_generation_gone",
        _arm,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.drain_dead_recovery.latest_validation_for_intent",
        lambda _intent_id: SimpleNamespace(code_ref="e5e747c2"),
    )


def _supervisor(
    store: RestartIntentStore,
    drain_state: Any,
    begin_drain: Any,
    kill: _Kill,
    start: _Start | None,
    *,
    deadline_s: float,
) -> GitWorkerDrainSupervisor:
    return GitWorkerDrainSupervisor(
        store=store,
        begin_drain=begin_drain,
        drain_state=drain_state,
        subscribe_events=_Feed(),
        kill=kill,
        start=start,
        deadline_s=deadline_s,
        reconcile_interval_s=0.01,
        progress_interval_s=999.0,
    )


def test_dead_process_drain_converges_to_start(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unreachable drain-state starts the worker and leaves pending_drain."""
    import scripts.model_manager.ui.controller.git_worker_drain_supervisor as sup_mod

    monkeypatch.setattr(sup_mod, "_PROBE_UNREACHABLE_WINDOW_S", 0.03)
    _patch_validate(monkeypatch)
    store = _store(tmp_path)
    intent = store.create_intent(
        service=_SERVICE, action="sync_restart", deadline_at="d", reason="r"
    )
    probes = {"n": 0}

    async def drain_state() -> dict[str, Any]:
        probes["n"] += 1
        if probes["n"] == 1:
            return _snap(draining=False, drain_epoch=0, active_count=1)
        raise RuntimeError("connection refused")

    async def begin_drain(_body: dict[str, Any]) -> dict[str, Any]:
        return _snap()

    kill = _Kill()
    start = _Start()
    sup = _supervisor(store, drain_state, begin_drain, kill, start, deadline_s=30.0)
    _run(asyncio.wait_for(sup.supervise(intent), timeout=2.0))
    assert start.calls == 1
    assert kill.calls == 0
    got = store.get(intent.intent_id)
    assert got is not None
    assert got.status == STATUS_VERIFYING_ACTIVATION
    assert got.status != STATUS_PENDING_DRAIN


def test_deadline_ceiling_with_live_occupants_stays_alert_only(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ceiling plus a live probe with occupants pages and does not start."""
    _patch_validate(monkeypatch)
    store = _store(tmp_path)
    intent = store.create_intent(
        service=_SERVICE, action="sync_restart", deadline_at="d", reason="r"
    )

    async def drain_state() -> dict[str, Any]:
        return _snap(active_count=1, pid=100)

    async def begin_drain(_body: dict[str, Any]) -> dict[str, Any]:
        return _snap()

    kill = _Kill()
    start = _Start()
    sup = _supervisor(store, drain_state, begin_drain, kill, start, deadline_s=0.05)

    async def _bounded() -> None:
        task = asyncio.create_task(sup.supervise(intent))
        await asyncio.sleep(0.4)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    _run(_bounded())
    assert start.calls == 0
    assert kill.calls == 0
    got = store.get(intent.intent_id)
    assert got is not None and got.status == STATUS_PENDING_DRAIN


def test_deadline_ceiling_force_starts_when_probe_is_dead(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ceiling on an unreachable worker starts it instead of paging forever."""
    _patch_validate(monkeypatch)
    store = _store(tmp_path)
    intent = store.create_intent(
        service=_SERVICE, action="sync_restart", deadline_at="d", reason="r"
    )
    probes = {"n": 0}

    async def drain_state() -> dict[str, Any]:
        probes["n"] += 1
        if probes["n"] == 1:
            return _snap(draining=False, drain_epoch=0, pid=100)
        raise RuntimeError("connection refused")

    async def begin_drain(_body: dict[str, Any]) -> dict[str, Any]:
        return _snap()

    kill = _Kill()
    start = _Start()
    sup = _supervisor(store, drain_state, begin_drain, kill, start, deadline_s=0.01)
    _run(asyncio.wait_for(sup.supervise(intent), timeout=2.0))
    assert start.calls == 1
    assert kill.calls == 0
    got = store.get(intent.intent_id)
    assert got is not None and got.status == STATUS_VERIFYING_ACTIVATION


def test_dead_pid_drain_converges_and_starts(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pid=null converges immediately and starts, without the heartbeat TTL."""
    _patch_validate(monkeypatch)
    store = _store(tmp_path)
    intent = store.create_intent(
        service=_SERVICE, action="sync_restart", deadline_at="d", reason="r"
    )
    probes = {"n": 0}

    async def drain_state() -> dict[str, Any]:
        probes["n"] += 1
        if probes["n"] == 1:
            return _snap(draining=False, drain_epoch=0, pid=100)
        return _snap(pid=None, active_count=1)

    async def begin_drain(_body: dict[str, Any]) -> dict[str, Any]:
        return _snap()

    kill = _Kill()
    start = _Start()
    sup = _supervisor(store, drain_state, begin_drain, kill, start, deadline_s=30.0)
    _run(asyncio.wait_for(sup.supervise(intent), timeout=2.0))
    assert start.calls == 1
    assert kill.calls == 0
    got = store.get(intent.intent_id)
    assert got is not None and got.status == STATUS_VERIFYING_ACTIVATION


def test_unreachable_probe_drain_converges_and_starts(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drain probe down for one heartbeat TTL starts; a live pid is not the fast path."""
    import scripts.model_manager.ui.controller.git_worker_drain_supervisor as sup_mod

    monkeypatch.setattr(sup_mod, "_PROBE_UNREACHABLE_WINDOW_S", 0.03)
    _patch_validate(monkeypatch)
    store = _store(tmp_path)
    intent = store.create_intent(
        service=_SERVICE, action="sync_restart", deadline_at="d", reason="r"
    )
    probes = {"n": 0}

    async def drain_state() -> dict[str, Any]:
        probes["n"] += 1
        if probes["n"] == 1:
            return _snap(draining=False, drain_epoch=0, pid=100)
        raise RuntimeError("connection refused")

    async def begin_drain(_body: dict[str, Any]) -> dict[str, Any]:
        return _snap(pid=100)

    kill = _Kill()
    start = _Start()
    sup = _supervisor(store, drain_state, begin_drain, kill, start, deadline_s=30.0)
    _run(asyncio.wait_for(sup.supervise(intent), timeout=2.0))
    assert start.calls == 1
    assert kill.calls == 0
    got = store.get(intent.intent_id)
    assert got is not None and got.status == STATUS_VERIFYING_ACTIVATION


def test_recycle_giw_starts_stopped_giw_under_active_drain(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """recycle_giw starts a stopped worker and does not enter the drain gate."""
    store = _store(tmp_path)
    intent = store.create_intent(
        service=_SERVICE, action="sync_restart", deadline_at="d", reason="wedged"
    )
    starts: list[str] = []

    async def _emit(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def _force(
        bound_store: RestartIntentStore,
        bound_intent: Any,
        start: Any,
        *,
        reason: str,
    ) -> str:
        assert reason == "recycle_stopped"
        message = await start()
        bound_store.advance(bound_intent.intent_id, status=STATUS_VERIFYING_ACTIVATION)
        return message

    def _drain_must_not_run(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("drain gate must not run when the worker is stopped")

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.giw_recycle.events.emit_manage_recycle_requested",
        _emit,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.giw_recycle.events.emit_manage_recycle_drain_attempted",
        _emit,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.giw_recycle.force_start_and_validate",
        _force,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.giw_recycle.run_gated_drain_supervised",
        _drain_must_not_run,
    )

    async def start_worker() -> str:
        starts.append("start")
        return "git-integration-worker starting"

    class _State:
        def check_git_integration_worker(self) -> SimpleNamespace:
            return SimpleNamespace(status=ServiceStatus.STOPPED)

    ctl = SimpleNamespace(
        service_state=_State(),
        start_git_integration_worker=start_worker,
        restart_intent_store=store,
    )
    result = _run(recycle_giw(ctl, {}, ""))
    assert result["status"] == "ok"
    assert result["state"] == "started"
    assert starts == ["start"]
    assert result["restart_intent_id"] == intent.intent_id
    got = store.get(intent.intent_id)
    assert got is not None and got.status == STATUS_VERIFYING_ACTIVATION


def test_paired_stop_then_start_watchdog_retries_when_start_gaps() -> None:
    """Stop runs, then start; a start that misses the gap is called again."""
    order: list[str] = []

    async def stop() -> str:
        order.append("stop")
        return "stopped"

    async def start() -> str:
        order.append("start")
        if order.count("start") == 1:
            await asyncio.sleep(5)
        return "up"

    result = _run(paired_stop_then_start(stop, start, gap_s=0.05))
    assert result == "up"
    assert order[0] == "stop"
    assert order.count("start") == 2
