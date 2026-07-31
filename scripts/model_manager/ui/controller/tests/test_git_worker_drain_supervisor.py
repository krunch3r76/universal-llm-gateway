"""Offline tests for the git-worker deferred-restart supervisor + intent store (P2).

Deterministic, no live services: a fake worker (begin-drain/drain-state), a fake
event feed, and a fake kill drive the supervisor; the real RestartIntentStore runs
against a tmp SQLite db. Async lifecycles are exercised via ``asyncio.run`` wrappers
in sync test functions (no pytest-asyncio dependency).

Covers AC-1..AC-9 of tasks/specs/git-worker-drain-p2-manage.md.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from scripts.model_manager.ui.controller.git_worker_drain_supervisor import (
    GitWorkerDrainSupervisor,
)
from scripts.model_manager.ui.controller.restart_intent_store import (
    STATUS_COMPLETED,
    STATUS_PENDING_DRAIN,
    STATUS_TIMEOUT,
    RestartIntentStore,
)

_SERVICE = "git_integration_worker"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.fixture
def events_log(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    """Capture emitted (signal, payload) instead of opening the events UDS."""
    log: list[tuple[str, dict[str, Any]]] = []

    async def _fake_emit(signal: str, payload: dict[str, Any], **_kw: Any) -> None:
        log.append((signal, payload))

    monkeypatch.setattr(
        "scripts.model_manager.observation_event._emit", _fake_emit
    )
    return log


def _store(tmp_path: Any) -> RestartIntentStore:
    return RestartIntentStore(db_path=tmp_path / "restart-intents.db")


def _snap(
    *,
    draining: bool,
    epoch: int,
    worker_id: str = "w1",
    started: str = "t1",
    active: int = 0,
    ops: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "draining": draining,
        "drain_epoch": epoch,
        "intent_id": "intent",
        "worker_id": worker_id,
        "pid": 100,
        "worker_started_at": started,
        "active_count": active,
        "active_ops": ops or [],
        "deadline_at": None,
    }


def _drain_completed(*, epoch: int, worker_id: str = "w1", seq: int = 1) -> dict[str, Any]:
    return {
        "signal": "git_worker.drain.completed",
        "seq": seq,
        "payload": {
            "drain_epoch": epoch,
            "worker_id": worker_id,
            "intent_id": "intent",
            "completed_at": "ts",
            "active_count": 0,
        },
    }


class _Feed:
    """subscribe_events factory yielding queued events then ending."""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = events

    def __call__(self, resume_seq: int) -> AsyncIterator[dict[str, Any]]:
        return self._gen()

    async def _gen(self) -> AsyncIterator[dict[str, Any]]:
        for ev in self._events:
            yield ev


class _Worker:
    def __init__(
        self, *, drain_states: list[dict[str, Any]], begin_snap: dict[str, Any]
    ) -> None:
        self._states = drain_states
        self._begin = begin_snap
        self.begun: list[dict[str, Any]] = []

    async def drain_state(self) -> dict[str, Any]:
        if len(self._states) > 1:
            return self._states.pop(0)
        return self._states[0]

    async def begin_drain(self, body: dict[str, Any]) -> dict[str, Any]:
        self.begun.append(body)
        return self._begin


class _Kill:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self) -> str:
        self.calls += 1
        return "git-integration-worker stopped (PID 100, 0.3s)."


def _supervisor(
    store: RestartIntentStore,
    worker: _Worker,
    feed: _Feed,
    kill: _Kill,
    *,
    deadline_s: float = 5.0,
) -> GitWorkerDrainSupervisor:
    return GitWorkerDrainSupervisor(
        store=store,
        begin_drain=worker.begin_drain,
        drain_state=worker.drain_state,
        subscribe_events=feed,
        kill=kill,
        deadline_s=deadline_s,
        reconcile_interval_s=0.01,
        progress_interval_s=999.0,
    )


# --------------------------------------------------------------------------- store


def test_create_intent_coalesces_one_live_per_service(tmp_path: Any) -> None:
    """AC-6: a second create while non-terminal returns the SAME intent_id."""
    store = _store(tmp_path)
    a = store.create_intent(
        service=_SERVICE, action="restart", deadline_at="d", reason="r1"
    )
    b = store.create_intent(
        service=_SERVICE, action="restart", deadline_at="d", reason="r2"
    )
    assert a.intent_id == b.intent_id
    assert a.status == STATUS_PENDING_DRAIN
    assert len(store.pending_intents()) == 1


def test_advance_terminal_frees_the_service(tmp_path: Any) -> None:
    store = _store(tmp_path)
    a = store.create_intent(
        service=_SERVICE, action="stop", deadline_at="d", reason="r"
    )
    store.advance(a.intent_id, status=STATUS_COMPLETED)
    assert store.active_for_service(_SERVICE) is None
    assert store.pending_intents() == []
    # A fresh create now succeeds with a new id.
    b = store.create_intent(
        service=_SERVICE, action="stop", deadline_at="d", reason="r"
    )
    assert b.intent_id != a.intent_id


def test_set_drain_epoch_and_last_seen_seq(tmp_path: Any) -> None:
    store = _store(tmp_path)
    a = store.create_intent(
        service=_SERVICE, action="restart", deadline_at="d", reason="r"
    )
    store.set_drain_epoch(
        a.intent_id, drain_epoch=3, worker_id="w9", worker_started_at="ts9"
    )
    store.set_last_seen_seq(a.intent_id, 42)
    got = store.get(a.intent_id)
    assert got is not None
    assert got.drain_epoch == 3
    assert got.worker_id == "w9"
    assert got.last_seen_event_seq == 42


# ----------------------------------------------------------------- supervisor


def test_event_drives_completion_and_sigterm(
    tmp_path: Any, events_log: list[tuple[str, dict[str, Any]]]
) -> None:
    """AC-2: drain.completed for the intent epoch+worker → epoch-check → SIGTERM."""
    store = _store(tmp_path)
    intent = store.create_intent(
        service=_SERVICE, action="restart", deadline_at="d", reason="r"
    )
    worker = _Worker(
        drain_states=[
            _snap(draining=False, epoch=0, active=1),  # begin-drain epoch read
            _snap(draining=True, epoch=1, active=1),  # await-loop reconcile (busy)
            _snap(draining=True, epoch=1, active=0),  # final epoch-check (idle)
        ],
        begin_snap=_snap(draining=True, epoch=1, active=1),
    )
    kill = _Kill()
    sup = _supervisor(
        store, worker, _Feed([_drain_completed(epoch=1, worker_id="w1")]), kill
    )

    _run(sup.supervise(intent))

    assert kill.calls == 1
    assert worker.begun and worker.begun[0]["drain_epoch"] == 1
    got = store.get(intent.intent_id)
    assert got is not None and got.status == STATUS_COMPLETED
    signals = [s for s, _ in events_log]
    assert "manage.restart.deferred" in signals
    assert "manage.restart.completed" in signals


def test_stale_event_ignored_then_times_out(
    tmp_path: Any, events_log: list[tuple[str, dict[str, Any]]]
) -> None:
    """AC-3/AC-4: a wrong-epoch event never converges → alert-only timeout, no kill."""
    store = _store(tmp_path)
    intent = store.create_intent(
        service=_SERVICE, action="restart", deadline_at="d", reason="r"
    )
    worker = _Worker(
        drain_states=[_snap(draining=True, epoch=1, active=1)],  # never idle
        begin_snap=_snap(draining=True, epoch=1, active=1),
    )
    kill = _Kill()
    # Event carries a STALE epoch (2) — must be ignored.
    sup = _supervisor(
        store,
        worker,
        _Feed([_drain_completed(epoch=2, worker_id="w1")]),
        kill,
        deadline_s=0.05,
    )

    _run(sup.supervise(intent))

    assert kill.calls == 0
    got = store.get(intent.intent_id)
    assert got is not None and got.status == STATUS_TIMEOUT
    assert "manage.restart.timeout" in [s for s, _ in events_log]


def test_final_check_fresh_generation_aborts_kill(
    tmp_path: Any, events_log: list[tuple[str, dict[str, Any]]]
) -> None:
    """AC-3: the worker restarted out from under us → no kill of a fresh worker."""
    store = _store(tmp_path)
    intent = store.create_intent(
        service=_SERVICE, action="restart", deadline_at="d", reason="r"
    )
    worker = _Worker(
        drain_states=[
            _snap(draining=False, epoch=0, active=1),  # begin-drain epoch read
            _snap(draining=True, epoch=1, active=1),  # await-loop reconcile (busy)
            # final epoch-check shows a DIFFERENT worker generation (w2/t2):
            _snap(draining=False, epoch=2, worker_id="w2", started="t2", active=0),
        ],
        begin_snap=_snap(draining=True, epoch=1, active=1),
    )
    kill = _Kill()
    sup = _supervisor(
        store, worker, _Feed([_drain_completed(epoch=1, worker_id="w1")]), kill
    )

    _run(sup.supervise(intent))

    assert kill.calls == 0  # never SIGTERM a fresh generation
    got = store.get(intent.intent_id)
    assert got is not None and got.status == STATUS_COMPLETED  # target already gone


def test_reconcile_reuses_stored_epoch_no_extra_begin(
    tmp_path: Any, events_log: list[tuple[str, dict[str, Any]]]
) -> None:
    """AC-5: a resumed intent re-drives begin-drain with the SAME epoch (idempotent)."""
    store = _store(tmp_path)
    intent = store.create_intent(
        service=_SERVICE, action="restart", deadline_at="d", reason="r"
    )
    # Simulate a prior generation having already begun the drain at epoch 7.
    store.set_drain_epoch(
        intent.intent_id, drain_epoch=7, worker_id="w1", worker_started_at="t1"
    )
    resumed = store.get(intent.intent_id)
    assert resumed is not None
    worker = _Worker(
        drain_states=[
            _snap(draining=True, epoch=7, active=1),  # await-loop reconcile (busy)
            _snap(draining=True, epoch=7, active=0),  # final epoch-check (idle)
        ],
        begin_snap=_snap(draining=True, epoch=7, active=0),
    )
    kill = _Kill()
    sup = _supervisor(
        store, worker, _Feed([_drain_completed(epoch=7, worker_id="w1")]), kill
    )

    _run(sup.supervise(resumed))

    # No drain-state read was needed to derive the epoch; begin-drain reused 7.
    assert worker.begun and worker.begun[0]["drain_epoch"] == 7
    assert kill.calls == 1
    got = store.get(intent.intent_id)
    assert got is not None and got.status == STATUS_COMPLETED
