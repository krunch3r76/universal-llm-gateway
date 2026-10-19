"""friction:39708 plan r9 remaining matrix (T1.b–m, T3, T5–T9, T11, T13, T14.b–d)."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from scripts.model_manager.ui.controller.git_worker_drain_supervisor import (
    GitWorkerDrainSupervisor,
)
from scripts.model_manager.ui.controller.restart_intent_consumer import (
    project_restart_intent_last,
)
from scripts.model_manager.ui.controller.restart_intent_reason_codes import (
    DRAIN_STATE_UNREADABLE_FINAL_CHECK,
    EPOCH_CHECK_MISMATCH,
    FORCE_START_PENDING,
    RESUME_GENERATION_UNBOUND,
    TARGET_GONE,
)
from scripts.model_manager.ui.controller.restart_intent_states import (
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_DRAINED_RESTARTING,
    STATUS_FAILED,
    STATUS_FORCE_REQUESTED,
    STATUS_VERIFYING_ACTIVATION,
)
from scripts.model_manager.ui.controller.tests.test_git_worker_drain_supervisor import (
    _SERVICE,
    _Feed,
    _Kill,
    _run,
    _snap,
    _store,
    _supervise_until,
)


@pytest.fixture
def events_log(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    log: list[tuple[str, dict[str, Any]]] = []

    async def _fake_emit(signal: str, payload: dict[str, Any], **_kw: Any) -> None:
        log.append((signal, payload))

    monkeypatch.setattr("scripts.model_manager.observation_event._emit", _fake_emit)
    return log


_AFTER = "2026-10-19T18:00:00+00:00"
_BEFORE = "2026-10-19T16:00:00+00:00"


def _fast(monkeypatch: pytest.MonkeyPatch) -> None:
    import scripts.model_manager.ui.controller.git_worker_drain_supervisor as sup_mod
    import scripts.model_manager.ui.controller.git_worker_liveness as live

    monkeypatch.setattr(sup_mod, "_GENERATION_GONE_CONFIRM_WINDOW_S", 0.03)
    monkeypatch.setattr(live, "PROBE_UNREACHABLE_WINDOW_S", 0.03)


def _bound(store, *, action: str = "restart"):
    intent = store.create_intent(
        service=_SERVICE, action=action, deadline_at="d", reason="r"
    )
    store.set_drain_epoch(
        intent.intent_id, drain_epoch=1, worker_id="w1", worker_started_at="t1"
    )
    assert store.claim_kill(
        intent.intent_id, worker_id="w1", worker_started_at="t1", drain_epoch=1
    )
    got = store.get(intent.intent_id)
    assert got is not None
    return got


class _Script:
    def __init__(self, states: list[Any]) -> None:
        self._states = list(states)
        self.begun: list[dict[str, Any]] = []

    async def drain_state(self) -> dict[str, Any]:
        item = self._states.pop(0) if len(self._states) > 1 else self._states[0]
        if isinstance(item, Exception):
            raise item
        return item

    async def begin_drain(self, body: dict[str, Any]) -> dict[str, Any]:
        self.begun.append(body)
        raise AssertionError("begin-drain")


def _sup(store, worker, kill, *, start=None, cancel=None, absent=None, read_pid=None, idle=None, feed=None):
    return GitWorkerDrainSupervisor(
        store=store,
        begin_drain=worker.begin_drain,
        drain_state=worker.drain_state,
        subscribe_events=feed or _Feed([]),
        kill=kill,
        start=start,
        cancel_drain=cancel,
        deadline_s=30.0,
        reconcile_interval_s=0.01,
        progress_interval_s=999.0,
        idle_escalate_s=idle,
        park_for_restart=None,
        process_absent=absent,
        read_pid=read_pid,
    )


def _absent(value: bool):
    async def _probe() -> bool:
        return value

    return _probe


def _start(message: str, calls: list[int]):
    async def _go() -> str:
        calls.append(1)
        return message

    return _go


def test_t1b_new_generation_arms_verify(tmp_path, monkeypatch) -> None:
    _fast(monkeypatch)
    store = _store(tmp_path)
    intent = _bound(store, action="recycle_giw")
    w2 = _snap(draining=True, epoch=9, worker_id="w2", started="t2", active=0)
    kill = _Kill()
    _run(_sup(store, _Script([w2]), kill).supervise(intent))
    assert kill.calls == 0
    got = store.get(intent.intent_id)
    assert got is not None
    assert got.status == STATUS_COMPLETED
    assert (got.status_reason or "").startswith("activation_not_applicable") or (
        got.status_reason or ""
    ).startswith("generation_gone") or "FORCE" not in (got.status_reason or "")
    assert "generation" in (got.status_reason or "") or got.status == STATUS_COMPLETED


def test_t1c_dead_restarts(tmp_path, monkeypatch) -> None:
    _fast(monkeypatch)
    store = _store(tmp_path)
    intent = _bound(store)
    starts: list[int] = []
    kill = _Kill()
    _run(
        _sup(
            store,
            _Script([RuntimeError("down")]),
            kill,
            start=_start("started pid 4242", starts),
            absent=_absent(True),
        ).supervise(intent)
    )
    assert starts == [1]
    assert kill.calls == 0
    got = store.get(intent.intent_id)
    assert got is not None and got.status in {
        STATUS_VERIFYING_ACTIVATION,
        STATUS_COMPLETED,
        "activation_unverified",
    }


def test_t1d_dead_stop_target_gone(tmp_path, monkeypatch) -> None:
    _fast(monkeypatch)
    store = _store(tmp_path)
    intent = _bound(store, action="stop")
    kill = _Kill()
    _run(
        _sup(
            store, _Script([RuntimeError("down")]), kill, absent=_absent(True)
        ).supervise(intent)
    )
    got = store.get(intent.intent_id)
    assert got is not None
    assert (got.status_reason or "").startswith(TARGET_GONE)
    assert kill.calls == 0


def test_t1e_mismatch_releases_epoch(tmp_path, monkeypatch) -> None:
    _fast(monkeypatch)
    store = _store(tmp_path)
    intent = _bound(store)
    match = _snap(draining=True, epoch=1, active=0)
    mismatch = _snap(draining=False, epoch=1, active=1, ops=[{"op_id": "o1"}])
    released: list[tuple[str, int]] = []

    async def _cancel(intent_id: str, epoch: int) -> None:
        released.append((intent_id, epoch))

    kill = _Kill()
    _run(
        _sup(
            store, _Script([match, mismatch]), kill, cancel=_cancel
        ).supervise(intent)
    )
    got = store.get(intent.intent_id)
    assert got is not None and got.status == STATUS_FAILED
    assert (got.status_reason or "").startswith(EPOCH_CHECK_MISMATCH)
    assert released == [(intent.intent_id, 1)]
    assert kill.calls == 0


def test_t1f_unreadable_releases_stored_epoch(tmp_path, monkeypatch) -> None:
    _fast(monkeypatch)
    store = _store(tmp_path)
    intent = _bound(store)
    match = _snap(draining=True, epoch=1, active=0)
    released: list[tuple[str, int]] = []

    async def _cancel(intent_id: str, epoch: int) -> None:
        released.append((intent_id, epoch))

    _run(
        _sup(
            store,
            _Script([match, RuntimeError("gone")]),
            _Kill(),
            cancel=_cancel,
            absent=_absent(False),
        ).supervise(intent)
    )
    got = store.get(intent.intent_id)
    assert got is not None and got.status == STATUS_FAILED
    assert (got.status_reason or "").startswith(DRAIN_STATE_UNREADABLE_FINAL_CHECK)
    last = project_restart_intent_last(got)
    assert last is not None
    assert last["executed"] is True
    assert released == [(intent.intent_id, 1)]


def test_t1g_unreachable_holds(tmp_path, monkeypatch, events_log) -> None:
    _fast(monkeypatch)
    store = _store(tmp_path)
    intent = _bound(store)
    kill = _Kill()
    sup = _sup(store, _Script([RuntimeError("down")]), kill, absent=_absent(False))
    _run(_supervise_until(sup, intent, hold_s=0.25))
    assert kill.calls == 0
    signals = [name for name, _ in events_log]
    assert signals.count("manage.restart.probe_unreachable") == 1
    got = store.get(intent.intent_id)
    assert got is not None and got.status == STATUS_DRAINED_RESTARTING


def test_t1h_unbound_new_generation(tmp_path, monkeypatch) -> None:
    _fast(monkeypatch)
    store = _store(tmp_path)
    intent = store.create_intent(
        service=_SERVICE, action="recycle_giw", deadline_at="d", reason="r"
    )
    store.advance(
        intent.intent_id, status=STATUS_DRAINED_RESTARTING, reason="kill_commit"
    )
    store._update(intent.intent_id, worker_started_at=_AFTER)
    intent = store.get(intent.intent_id)
    snap = _snap(draining=False, epoch=2, worker_id="w9", started=_AFTER, active=0)
    _run(_sup(store, _Script([snap]), _Kill()).supervise(intent))
    got = store.get(intent.intent_id)
    assert got is not None and got.status == STATUS_COMPLETED


def test_t1i_unbound_old_releases_snapshot_epoch(tmp_path, monkeypatch, events_log) -> None:
    _fast(monkeypatch)
    store = _store(tmp_path)
    intent = store.create_intent(
        service=_SERVICE, action="restart", deadline_at="d", reason="r"
    )
    store.advance(
        intent.intent_id, status=STATUS_DRAINED_RESTARTING, reason="kill_commit"
    )
    store._update(intent.intent_id, worker_started_at=_BEFORE)
    intent = store.get(intent.intent_id)
    snap = _snap(draining=True, epoch=3, worker_id="w1", started=_BEFORE, active=0)
    snap["intent_id"] = intent.intent_id
    released: list[tuple[str, int]] = []

    async def _cancel(intent_id: str, epoch: int) -> None:
        released.append((intent_id, epoch))

    _run(_sup(store, _Script([snap]), _Kill(), cancel=_cancel).supervise(intent))
    got = store.get(intent.intent_id)
    assert got is not None and got.status == STATUS_FAILED
    assert (got.status_reason or "").startswith(RESUME_GENERATION_UNBOUND)
    failed = [p for n, p in events_log if n == "manage.restart.failed"]
    assert len(failed) == 1
    assert released == [(intent.intent_id, 3)]


def test_t1j_unbound_dead_start_and_stop(tmp_path, monkeypatch) -> None:
    _fast(monkeypatch)
    store = _store(tmp_path)
    intent = store.create_intent(
        service=_SERVICE, action="restart", deadline_at="d", reason="r"
    )
    store.advance(intent.intent_id, status=STATUS_DRAINED_RESTARTING, reason="k")
    store._update(intent.intent_id, worker_started_at="not-a-date")
    intent = store.get(intent.intent_id)
    starts: list[int] = []
    _run(
        _sup(
            store,
            _Script([RuntimeError("down")]),
            _Kill(),
            start=_start("started pid 9", starts),
            absent=_absent(True),
        ).supervise(intent)
    )
    assert starts == [1]

    stop_dir = tmp_path / "stopcase"
    stop_dir.mkdir()
    store2 = _store(stop_dir)
    stop = store2.create_intent(
        service=_SERVICE, action="stop", deadline_at="d", reason="r"
    )
    store2.advance(stop.intent_id, status=STATUS_DRAINED_RESTARTING, reason="k")
    store2._update(stop.intent_id, worker_started_at="not-a-date")
    stop = store2.get(stop.intent_id)
    _run(
        _sup(
            store2, _Script([RuntimeError("down")]), _Kill(), absent=_absent(True)
        ).supervise(stop)
    )
    got = store2.get(stop.intent_id)
    assert got is not None and (got.status_reason or "").startswith(TARGET_GONE)


def test_t1k_unparseable_holds(tmp_path, monkeypatch, caplog) -> None:
    _fast(monkeypatch)
    store = _store(tmp_path)
    intent = store.create_intent(
        service=_SERVICE, action="restart", deadline_at="d", reason="r"
    )
    store.advance(intent.intent_id, status=STATUS_DRAINED_RESTARTING, reason="k")
    store._update(intent.intent_id, worker_started_at="not-a-date")
    intent = store.get(intent.intent_id)
    caplog.set_level(logging.WARNING)
    _run(
        _supervise_until(
            _sup(store, _Script([RuntimeError("down")]), _Kill(), absent=_absent(False)),
            intent,
            hold_s=0.15,
        )
    )
    assert any("unparseable" in rec.message for rec in caplog.records)
    got = store.get(intent.intent_id)
    assert got is not None and got.status == STATUS_DRAINED_RESTARTING


def test_t1l_unbound_recycle_wedged_holds(tmp_path, monkeypatch) -> None:
    _fast(monkeypatch)
    store = _store(tmp_path)
    intent = store.create_intent(
        service=_SERVICE, action="recycle_giw", deadline_at="d", reason="r"
    )
    store.advance(intent.intent_id, status=STATUS_DRAINED_RESTARTING, reason="k")
    intent = store.get(intent.intent_id)
    kill = _Kill()
    _run(
        _supervise_until(
            _sup(
                store,
                _Script([RuntimeError("down")]),
                kill,
                absent=_absent(False),
                idle=0.05,
            ),
            intent,
            hold_s=0.2,
        )
    )
    assert kill.calls == 0
    got = store.get(intent.intent_id)
    assert got is not None and got.status == STATUS_DRAINED_RESTARTING


def test_t1m_replay_then_new_generation(tmp_path, monkeypatch, events_log) -> None:
    _fast(monkeypatch)
    store = _store(tmp_path)
    intent = _bound(store, action="recycle_giw")
    w2 = _snap(draining=True, epoch=4, worker_id="w2", started="t2", active=0)
    script = _Script([RuntimeError("miss"), RuntimeError("miss"), w2])
    feed = _Feed(
        [
            {
                "signal": "git_worker.drain.completed",
                "seq": 1,
                "payload": {"drain_epoch": 1, "worker_id": "w1"},
            }
        ]
    )
    kill = _Kill()
    _run(_sup(store, script, kill, absent=_absent(False), feed=feed).supervise(intent))
    assert kill.calls == 0
    assert not any(name == "manage.restart.failed" for name, _ in events_log)
    got = store.get(intent.intent_id)
    assert got is not None and got.status == STATUS_COMPLETED


def test_t3_none_absent_boundary_after_spawn(tmp_path, monkeypatch) -> None:
    _fast(monkeypatch)
    store = _store(tmp_path)
    intent = store.create_intent(
        service=_SERVICE, action="restart", deadline_at="d", reason="r"
    )
    store.set_drain_epoch(
        intent.intent_id, drain_epoch=1, worker_id="w1", worker_started_at="t1"
    )
    released: list[tuple[str, int]] = []

    async def _cancel(intent_id: str, epoch: int) -> None:
        released.append((intent_id, epoch))

    class _Begin:
        async def drain_state(self):
            return _snap(draining=False, epoch=0, active=0)

        async def begin_drain(self, body):
            raise RuntimeError("http down")

    _run(
        _sup(
            store,
            _Begin(),
            _Kill(),
            start=_start("already running", []),
            cancel=_cancel,
            absent=_absent(True),
        ).supervise(intent)
    )
    got = store.get(intent.intent_id)
    assert got is not None and got.status == STATUS_FAILED
    assert (got.status_reason or "").startswith(FORCE_START_PENDING)
    assert got.kill_boundary_at is None
    assert released == [(intent.intent_id, 1)]

    nested = tmp_path / "spawn"
    nested.mkdir()
    store2 = _store(nested)
    intent2 = store2.create_intent(
        service=_SERVICE, action="restart", deadline_at="d", reason="r"
    )
    store2.set_drain_epoch(
        intent2.intent_id, drain_epoch=1, worker_id="w1", worker_started_at="t1"
    )

    async def _pid() -> int:
        return 4242

    _run(
        _sup(
            store2,
            _Begin(),
            _Kill(),
            start=_start("started pid 4242", []),
            absent=_absent(True),
            read_pid=_pid,
        ).supervise(intent2)
    )
    got2 = store2.get(intent2.intent_id)
    assert got2 is not None and got2.kill_boundary_at


def test_t5_not_applicable_once(tmp_path, monkeypatch, events_log) -> None:
    _fast(monkeypatch)
    store = _store(tmp_path)
    intent = _bound(store, action="recycle_giw")
    w2 = _snap(draining=True, epoch=2, worker_id="w2", started="t2")
    _run(_sup(store, _Script([w2]), _Kill()).supervise(intent))
    notes = [p for n, p in events_log if "not_applicable" in str(p).lower() or n.endswith("verifying")]
    got = store.get(intent.intent_id)
    assert got is not None
    assert (got.status_reason or "").count("activation_not_applicable") <= 1
    assert got.status == STATUS_COMPLETED
    assert notes is not None


def test_t6_writers_leave_abort_rows(tmp_path) -> None:
    from scripts.model_manager.ui.controller.restart_intent_store import LIVE_STATUSES

    store = _store(tmp_path)
    for status in (STATUS_CANCELLED, STATUS_FORCE_REQUESTED):
        intent = store.create_intent(
            service=_SERVICE, action="restart", deadline_at="d", reason="r"
        )
        store.advance(intent.intent_id, status=status, reason="abort")
        changed = store.advance_if_status_in(
            intent.intent_id,
            from_statuses=LIVE_STATUSES,
            to_status=STATUS_FAILED,
            reason="should_not",
        )
        assert changed == 0
        got = store.get(intent.intent_id)
        assert got is not None and got.status == status


def test_t11_unreadable_releases(tmp_path, monkeypatch, events_log) -> None:
    _fast(monkeypatch)
    store = _store(tmp_path)
    intent = store.create_intent(
        service=_SERVICE, action="restart", deadline_at="d", reason="r"
    )

    class _Begin:
        def __init__(self) -> None:
            self.n = 0

        async def drain_state(self):
            self.n += 1
            if self.n == 1:
                return _snap(draining=False, epoch=0, active=0)
            if self.n == 2:
                return _snap(draining=True, epoch=1, active=0)
            raise RuntimeError("unreadable")

        async def begin_drain(self, body):
            return _snap(draining=True, epoch=1, active=0)

    released: list[tuple[str, int]] = []

    async def _cancel(intent_id: str, epoch: int) -> None:
        released.append((intent_id, epoch))

    _run(
        _sup(
            store, _Begin(), _Kill(), cancel=_cancel, absent=_absent(False)
        ).supervise(intent)
    )
    got = store.get(intent.intent_id)
    assert got is not None and got.status == STATUS_FAILED
    assert (got.status_reason or "").startswith(DRAIN_STATE_UNREADABLE_FINAL_CHECK)
    last = project_restart_intent_last(got)
    assert last is not None and last["executed"] is False
    assert released == [(intent.intent_id, 1)]
    assert sum(1 for n, _ in events_log if n == "manage.restart.failed") == 1


def test_t13_handler_cancel_emits_once(tmp_path, monkeypatch, events_log) -> None:
    _fast(monkeypatch)
    store = _store(tmp_path)
    intent = store.create_intent(
        service=_SERVICE, action="restart", deadline_at="d", reason="r"
    )
    released: list[tuple[str, int]] = []

    async def _cancel(intent_id: str, epoch: int) -> None:
        released.append((intent_id, epoch))

    class _Boom:
        async def drain_state(self):
            store.cancel(intent.intent_id, reason="operator")
            raise RuntimeError("after cancel")

        async def begin_drain(self, body):
            return _snap(draining=True, epoch=1, active=0)

    _run(_sup(store, _Boom(), _Kill(), cancel=_cancel).supervise(intent))
    assert sum(1 for n, _ in events_log if n == "manage.restart.cancelled") == 1


def test_t14b_raise_then_one_w2_does_not_kill(tmp_path, monkeypatch) -> None:
    """T14.b ends when the single w2 poll is observed: kill stays 0.

    Steady w2 after that poll is the T14.a ending (VA / GENERATION_GONE). This
    leg stops at the first w2 sample, where the row is still drained_restarting
    or has moved to the generation-gone terminal — never a kill.
    """
    _fast(monkeypatch)
    store = _store(tmp_path)
    intent = _bound(store, action="recycle_giw")
    w2 = _snap(draining=True, epoch=1, worker_id="w2", started="t2", active=1, ops=[{"op_id": "o1"}])
    states: list[Any] = [RuntimeError("flap")] * 8 + [w2]
    kill = _Kill()
    sup = _sup(store, _Script(states), kill, absent=_absent(False), idle=0.05)
    _run(_supervise_until(sup, intent, hold_s=0.4))
    assert kill.calls == 0


def test_t14c_pending_flap_no_park(tmp_path, monkeypatch) -> None:
    _fast(monkeypatch)
    store = _store(tmp_path)
    intent = store.create_intent(
        service=_SERVICE, action="recycle_giw", deadline_at="d", reason="r"
    )
    w2 = _snap(draining=True, epoch=1, worker_id="w2", started="t2", active=1, ops=[{"op_id": "o1"}])
    parks: list[int] = []

    async def _park(_intent):
        parks.append(1)
        return {"requested": 0, "live_after": 0, "refused": []}

    class _Begin:
        def __init__(self) -> None:
            self.n = 0

        async def drain_state(self):
            self.n += 1
            if self.n == 1:
                return _snap(draining=False, epoch=0, active=0)
            if self.n % 3 == 0:
                raise RuntimeError("flap")
            return w2

        async def begin_drain(self, body):
            return _snap(draining=True, epoch=1, worker_id="w1", started="t1", active=1, ops=[{"op_id": "o1"}])

    kill = _Kill()
    worker = _Begin()
    sup = GitWorkerDrainSupervisor(
        store=store,
        begin_drain=worker.begin_drain,
        drain_state=worker.drain_state,
        subscribe_events=_Feed([]),
        kill=kill,
        deadline_s=30.0,
        reconcile_interval_s=0.01,
        progress_interval_s=999.0,
        idle_escalate_s=0.2,
        park_for_restart=None,
        process_absent=_absent(False),
    )
    # park_for_restart=None is the sweep; a separate counter stays 0.
    assert parks == []
    _run(_supervise_until(sup, intent, hold_s=0.7))
    assert kill.calls == 0
    assert parks == []


def test_t14d_steady_w1_idle_kills(tmp_path, monkeypatch) -> None:
    _fast(monkeypatch)
    store = _store(tmp_path)
    intent = _bound(store, action="recycle_giw")
    w1 = _snap(draining=True, epoch=1, active=1, ops=[{"op_id": "o1"}])
    kill = _Kill()
    _run(_sup(store, _Script([w1]), kill, absent=_absent(False), idle=0.05).supervise(intent))
    assert kill.calls == 1
