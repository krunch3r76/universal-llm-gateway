"""Dead-target drain recovery: start, don't keep-await a corpse."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.model_manager.ui.controller import git_worker_liveness as live_mod
from scripts.model_manager.ui.controller.drain_dead_recovery import (
    drain_supervisor_start,
    force_start_and_validate,
    lifecycle_result_certifies,
    paired_stop_then_start,
)
from scripts.model_manager.ui.controller.git_worker_drain_supervisor import (
    GitWorkerDrainSupervisor,
)
from scripts.model_manager.ui.controller.giw_recycle import recycle_giw
from scripts.model_manager.ui.controller.restart_intent_states import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PENDING_DRAIN,
    STATUS_VERIFYING_ACTIVATION,
)
from scripts.model_manager.ui.controller.restart_intent_store import RestartIntentStore
from scripts.model_manager.ui.model.service_state import ServiceStatus
from services.git_integration_worker.relay.propagation_probe import (
    resolve_identity_measurement,
)

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
        store.advance(
            intent.intent_id, status=STATUS_VERIFYING_ACTIVATION, reason="test"
        )
        return True

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.drain_dead_recovery.arm_verify_after_generation_gone",
        _arm,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.drain_dead_recovery.latest_validation_for_intent",
        lambda _intent_id: SimpleNamespace(code_ref="e5e747c2"),
    )


async def _absent() -> bool:
    return True


async def _present() -> bool:
    return False


def _supervisor(
    store: RestartIntentStore,
    drain_state: Any,
    begin_drain: Any,
    kill: _Kill,
    start: _Start | None,
    *,
    deadline_s: float,
    process_absent: Any = None,
    read_pid: Any = None,
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
        process_absent=process_absent,
        read_pid=read_pid,
    )


def test_dead_process_drain_converges_to_start(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unreachable drain-state plus an absent health probe starts the worker."""
    monkeypatch.setattr(live_mod, "PROBE_UNREACHABLE_WINDOW_S", 0.03)
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
    sup = _supervisor(
        store,
        drain_state,
        begin_drain,
        kill,
        start,
        deadline_s=30.0,
        process_absent=_absent,
    )
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
    """Ceiling starts only after the same miss window and an absent health probe."""
    monkeypatch.setattr(live_mod, "PROBE_UNREACHABLE_WINDOW_S", 0.03)
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
    sup = _supervisor(
        store,
        drain_state,
        begin_drain,
        kill,
        start,
        deadline_s=0.01,
        process_absent=_absent,
    )
    _run(asyncio.wait_for(sup.supervise(intent), timeout=2.0))
    assert start.calls == 1
    assert kill.calls == 0
    got = store.get(intent.intent_id)
    assert got is not None and got.status == STATUS_VERIFYING_ACTIVATION


def test_snapshot_with_null_pid_does_not_start(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A snapshot that arrived is not dead, even when its pid field is null."""
    _patch_validate(monkeypatch)
    store = _store(tmp_path)
    intent = store.create_intent(
        service=_SERVICE, action="sync_restart", deadline_at="d", reason="r"
    )

    async def drain_state() -> dict[str, Any]:
        return _snap(pid=None, active_count=1)

    async def begin_drain(_body: dict[str, Any]) -> dict[str, Any]:
        return _snap()

    kill = _Kill()
    start = _Start()
    sup = _supervisor(
        store,
        drain_state,
        begin_drain,
        kill,
        start,
        deadline_s=30.0,
        process_absent=_absent,
    )

    async def _bounded() -> None:
        task = asyncio.create_task(sup.supervise(intent))
        await asyncio.sleep(0.2)
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


def test_unreachable_probe_drain_converges_and_starts(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drain probe down for the confirm window, with health absent, starts."""
    monkeypatch.setattr(live_mod, "PROBE_UNREACHABLE_WINDOW_S", 0.03)
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
    sup = _supervisor(
        store,
        drain_state,
        begin_drain,
        kill,
        start,
        deadline_s=30.0,
        process_absent=_absent,
    )
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

    joined = {"n": 0}

    async def _join(service: str, *, join_s: float = 0.5) -> None:
        joined["n"] += 1
        assert service == _SERVICE

    async def _force(
        bound_store: RestartIntentStore,
        bound_intent: Any,
        start: Any,
        *,
        reason: str,
        **_kwargs: Any,
    ) -> str:
        assert reason == "recycle_stopped"
        message = await start()
        bound_store.advance(
            bound_intent.intent_id, status=STATUS_VERIFYING_ACTIVATION, reason="test"
        )
        return message

    async def _no_snapshot(_url: str) -> None:
        return None

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
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.giw_recycle.cancel_or_join_supervise_task",
        _join,
    )
    monkeypatch.setattr(live_mod, "PROBE_UNREACHABLE_WINDOW_S", 0.0)
    monkeypatch.setattr(live_mod, "probe_drain_snapshot", _no_snapshot)

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
    assert joined["n"] == 1
    got = store.get(intent.intent_id)
    assert got is not None and got.status == STATUS_VERIFYING_ACTIVATION


def test_paired_stop_then_start_joins_slow_start_without_retry() -> None:
    """A start still in flight after the gap is joined, not spawned again."""
    order: list[str] = []

    async def stop() -> str:
        order.append("stop")
        return "git-integration-worker stopped (PID 9, 0.2s)."

    async def start() -> str:
        order.append("start")
        await asyncio.sleep(0.1)
        return "up"

    result = _run(paired_stop_then_start(stop, start, gap_s=0.02))
    assert result == "up"
    assert order[0] == "stop"
    assert order.count("start") == 1


def test_stop_action_omits_start_callable() -> None:
    """Finding 1: stop does not receive a start callable."""
    start = _Start()

    async def _start() -> str:
        return await start()

    assert drain_supervisor_start("stop", _start) is None
    assert drain_supervisor_start("restart", _start) is _start
    assert drain_supervisor_start("sync_restart", _start) is _start
    assert drain_supervisor_start("recycle_giw", _start) is _start


def test_stop_action_dead_probe_completes_without_start(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stop drain whose target is gone does not start it."""
    monkeypatch.setattr(live_mod, "PROBE_UNREACHABLE_WINDOW_S", 0.03)
    _patch_validate(monkeypatch)
    store = _store(tmp_path)
    intent = store.create_intent(
        service=_SERVICE, action="stop", deadline_at="d", reason="r"
    )
    probes = {"n": 0}

    async def drain_state() -> dict[str, Any]:
        probes["n"] += 1
        if probes["n"] == 1:
            return _snap(draining=False, drain_epoch=0, active_count=0)
        raise RuntimeError("connection refused")

    async def begin_drain(_body: dict[str, Any]) -> dict[str, Any]:
        return _snap()

    kill = _Kill()
    start = _Start()
    sup = _supervisor(
        store,
        drain_state,
        begin_drain,
        kill,
        start,
        deadline_s=30.0,
        process_absent=_absent,
    )
    _run(asyncio.wait_for(sup.supervise(intent), timeout=2.0))
    assert start.calls == 0
    assert kill.calls == 0
    got = store.get(intent.intent_id)
    assert got is not None and got.status == STATUS_COMPLETED


def test_ceiling_single_miss_does_not_start(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding 2: one unanswered ceiling probe does not start a live worker."""
    monkeypatch.setattr(live_mod, "PROBE_UNREACHABLE_WINDOW_S", 0.05)
    _patch_validate(monkeypatch)
    store = _store(tmp_path)
    intent = store.create_intent(
        service=_SERVICE, action="sync_restart", deadline_at="d", reason="r"
    )
    probes = {"n": 0}

    async def drain_state() -> dict[str, Any]:
        probes["n"] += 1
        if probes["n"] == 2:
            raise RuntimeError("blip")
        return _snap(pid=100, active_count=1)

    async def begin_drain(_body: dict[str, Any]) -> dict[str, Any]:
        return _snap()

    kill = _Kill()
    start = _Start()
    sup = _supervisor(
        store,
        drain_state,
        begin_drain,
        kill,
        start,
        deadline_s=0.0,
        process_absent=_absent,
    )

    async def _bounded() -> None:
        task = asyncio.create_task(sup.supervise(intent))
        await asyncio.sleep(0.3)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    _run(_bounded())
    assert start.calls == 0
    assert kill.calls == 0


def test_unreachable_but_health_present_does_not_start(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Misses without an absent health/pid probe do not start."""
    monkeypatch.setattr(live_mod, "PROBE_UNREACHABLE_WINDOW_S", 0.03)
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
    sup = _supervisor(
        store,
        drain_state,
        begin_drain,
        kill,
        start,
        deadline_s=0.05,
        process_absent=_present,
    )

    async def _bounded() -> None:
        task = asyncio.create_task(sup.supervise(intent))
        await asyncio.sleep(0.3)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    _run(_bounded())
    assert start.calls == 0
    assert kill.calls == 0


def test_force_start_already_running_does_not_arm(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding 4: a no-op start does not arm activation verify."""

    async def _arm(*_args: Any, **_kwargs: Any) -> bool:
        raise AssertionError("must not arm verify")

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.drain_dead_recovery.arm_verify_after_generation_gone",
        _arm,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.drain_dead_recovery.latest_validation_for_intent",
        lambda _intent_id: SimpleNamespace(code_ref="e5e747c2"),
    )
    store = _store(tmp_path)
    intent = store.create_intent(
        service=_SERVICE, action="sync_restart", deadline_at="d", reason="r"
    )

    async def start() -> str:
        return "git-integration-worker is already running."

    message = _run(force_start_and_validate(store, intent, start, reason="dead_target"))
    assert "already running" in message
    got = store.get(intent.intent_id)
    assert got is not None and got.status == STATUS_FAILED


def test_force_start_same_pid_does_not_arm(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding 4: a re-read pid that matches the prior pid does not arm verify."""

    async def _arm(*_args: Any, **_kwargs: Any) -> bool:
        raise AssertionError("must not arm verify")

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.drain_dead_recovery.arm_verify_after_generation_gone",
        _arm,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.drain_dead_recovery.latest_validation_for_intent",
        lambda _intent_id: SimpleNamespace(code_ref="e5e747c2"),
    )
    store = _store(tmp_path)
    intent = store.create_intent(
        service=_SERVICE, action="restart", deadline_at="d", reason="r"
    )

    async def start() -> str:
        return "git-integration-worker starting (PID 100)."

    async def read_pid() -> int:
        return 100

    _run(
        force_start_and_validate(
            store,
            intent,
            start,
            reason="dead_target",
            prior_pid=100,
            read_pid=read_pid,
        )
    )
    got = store.get(intent.intent_id)
    assert got is not None and got.status == STATUS_FAILED


def test_unconfirmed_stop_does_not_start() -> None:
    """Finding 6: an unconfirmed stop does not call start."""
    order: list[str] = []

    async def stop() -> str:
        order.append("stop")
        return (
            "git-integration-worker may still be running "
            "(could not confirm death, PID 9, 12.0s)."
        )

    async def start() -> str:
        order.append("start")
        return "up"

    result = _run(paired_stop_then_start(stop, start, gap_s=1.0))
    assert "could not confirm death" in result
    assert order == ["stop"]


def test_lifecycle_result_refuses_unconfirmed_and_failed_start() -> None:
    """Finding 6: those return strings do not certify the intent."""
    unconfirmed = (
        "git-integration-worker may still be running "
        "(could not confirm death, PID 9, 12.0s)."
    )
    assert lifecycle_result_certifies(unconfirmed, action="restart") is False
    assert (
        lifecycle_result_certifies(
            "git-integration-worker failed (exit 1).", action="sync_restart"
        )
        is False
    )
    assert (
        lifecycle_result_certifies(
            "git-integration-worker is already running.", action="restart"
        )
        is False
    )
    assert (
        lifecycle_result_certifies(
            "git-integration-worker starting (PID 42).", action="restart"
        )
        is True
    )
    assert (
        lifecycle_result_certifies(
            "git-integration-worker stopped (PID 9, 0.4s).", action="stop"
        )
        is True
    )


def test_identity_measurement_compares_pids() -> None:
    """Finding 4: an unchanged pid is absent; a changed pid is measured."""
    same = resolve_identity_measurement(
        {"proof_before": {"pid": 7}, "pid": 7},
        service=_SERVICE,
        proof_class="process_live",
        code_ref="abc",
    )
    changed = resolve_identity_measurement(
        {"proof_before": {"pid": 7}, "pid": 8},
        service=_SERVICE,
        proof_class="process_live",
        code_ref="abc",
    )
    assert same == "absent"
    assert changed == "measured"


def test_predicate_requires_window_and_absent_probe() -> None:
    """The shared predicate is the conjunction, not either half alone."""
    assert (
        live_mod.drain_target_is_dead(
            consecutive_snapshot_misses=1,
            miss_threshold=45,
            health_pid_absent=True,
        )
        is False
    )
    assert (
        live_mod.drain_target_is_dead(
            consecutive_snapshot_misses=45,
            miss_threshold=45,
            health_pid_absent=False,
        )
        is False
    )
    assert (
        live_mod.drain_target_is_dead(
            consecutive_snapshot_misses=45,
            miss_threshold=45,
            health_pid_absent=True,
        )
        is True
    )
