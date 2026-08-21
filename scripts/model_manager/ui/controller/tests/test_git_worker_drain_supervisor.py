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

from scripts.model_manager.ui.api_dispatch import orchestrate_cancel_restart_intent
from scripts.model_manager.ui.controller.git_worker_drain_supervisor import (
    GitWorkerDrainSupervisor,
)
from scripts.model_manager.ui.controller.restart_intent_store import (
    STATUS_ACTIVATION_UNVERIFIED,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_DRAINED_RESTARTING,
    STATUS_PENDING_DRAIN,
    STATUS_TIMEOUT,
    STATUS_VERIFYING_ACTIVATION,
    RestartIntentCancelError,
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

    monkeypatch.setattr("scripts.model_manager.observation_event._emit", _fake_emit)
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


def _drain_completed(
    *, epoch: int, worker_id: str = "w1", seq: int = 1
) -> dict[str, Any]:
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
    stall_window_s: float = 15.0,
    idle_escalate_s: float | None = None,
) -> GitWorkerDrainSupervisor:
    return GitWorkerDrainSupervisor(
        store=store,
        begin_drain=worker.begin_drain,
        drain_state=worker.drain_state,
        subscribe_events=feed,
        kill=kill,
        deadline_s=deadline_s,
        stall_window_s=stall_window_s,
        reconcile_interval_s=0.01,
        progress_interval_s=999.0,
        idle_escalate_s=idle_escalate_s,
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
    assert got is not None and got.status in {
        STATUS_VERIFYING_ACTIVATION,
        STATUS_ACTIVATION_UNVERIFIED,
    }
    signals = [s for s, _ in events_log]
    assert "manage.restart.deferred" in signals
    assert "manage.restart.completed" in signals


def test_stale_event_timeout_then_stall_force_kill(
    tmp_path: Any, events_log: list[tuple[str, dict[str, Any]]]
) -> None:
    """Wrong-epoch event never converges: timeout alerts, then R1′ stall force-kills."""
    store = _store(tmp_path)
    intent = store.create_intent(
        service=_SERVICE, action="restart", deadline_at="d", reason="r"
    )
    stuck = _snap(
        draining=True,
        epoch=1,
        active=1,
        ops=[{"op_id": "stuck-ticket"}],
    )
    worker = _Worker(
        drain_states=[stuck],
        begin_snap=stuck,
    )
    kill = _Kill()
    sup = _supervisor(
        store,
        worker,
        _Feed([_drain_completed(epoch=2, worker_id="w1")]),
        kill,
        deadline_s=0.05,
        stall_window_s=0.08,
    )

    _run(sup.supervise(intent))

    assert kill.calls == 1
    got = store.get(intent.intent_id)
    assert got is not None
    assert got.status in {STATUS_VERIFYING_ACTIVATION, STATUS_ACTIVATION_UNVERIFIED}
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
    assert got is not None and got.status in {
        STATUS_COMPLETED,
        STATUS_ACTIVATION_UNVERIFIED,
        STATUS_VERIFYING_ACTIVATION,
    }  # target already gone; no-validation arm → unverified


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
    assert got is not None and got.status in {
        STATUS_VERIFYING_ACTIVATION,
        STATUS_ACTIVATION_UNVERIFIED,
    }


def test_settle_not_invoked_inline_from_supervisor(
    tmp_path: Any,
    events_log: list[tuple[str, dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Propagation settle is activation-verify-owned, not inline after kill."""
    captured: list[float | None] = []

    def _fake_settle(
        service: str,
        probe: Any,
        *,
        defer_if_unreachable: bool = True,
        settle_not_before_monotonic: float | None = None,
    ) -> list[Any]:
        captured.append(settle_not_before_monotonic)
        return []

    monkeypatch.setattr(
        "charter_runner_store.propagation_terminal.settle_open_rows_for_service",
        _fake_settle,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.git_worker_activation_verify.schedule_activation_verify",
        lambda *args, **kwargs: None,
    )
    store = _store(tmp_path)
    intent = store.create_intent(
        service=_SERVICE, action="restart", deadline_at="d", reason="r"
    )
    drain_started_mono = 1000.0
    worker = _Worker(
        drain_states=[
            _snap(draining=False, epoch=0, active=1),
            _snap(draining=True, epoch=1, active=0),
        ],
        begin_snap={
            **_snap(draining=True, epoch=1, active=0),
            "drain_started_monotonic": drain_started_mono,
        },
    )
    kill = _Kill()
    sup = _supervisor(
        store, worker, _Feed([_drain_completed(epoch=1, worker_id="w1")]), kill
    )

    _run(sup.supervise(intent))

    assert captured == []


# ------------------------------------------------ A′ cancel verb (AC1–AC6)


def test_store_cancel_writes_cancelled_pre_and_post_epoch(tmp_path: Any) -> None:
    """AC1/AC4: cancel helper writes STATUS_CANCELLED; fixture yields cancelled rows."""
    store = _store(tmp_path)
    pre = store.create_intent(
        service=_SERVICE, action="restart", deadline_at="d", reason="pre"
    )
    cancelled_pre = store.cancel(pre.intent_id)
    assert cancelled_pre.status == STATUS_CANCELLED
    assert store.get(pre.intent_id) is not None
    assert store.get(pre.intent_id).status == STATUS_CANCELLED  # type: ignore[union-attr]
    assert store.cancel(pre.intent_id).status == STATUS_CANCELLED  # idempotent

    post = store.create_intent(
        service=_SERVICE, action="restart", deadline_at="d", reason="post"
    )
    store.set_drain_epoch(
        post.intent_id, drain_epoch=2, worker_id="w1", worker_started_at="t1"
    )
    cancelled_post = store.cancel(post.intent_id)
    assert cancelled_post.status == STATUS_CANCELLED
    assert cancelled_post.drain_epoch == 2

    # AC4: fixture produces non-empty cancelled rows (contrast live CANCELLED_ROWS: []).
    import sqlite3

    with sqlite3.connect(store._db_path) as conn:
        rows = conn.execute(
            "SELECT intent_id FROM restart_intents WHERE status=?",
            (STATUS_CANCELLED,),
        ).fetchall()
    assert len(rows) >= 2


def test_store_cancel_refused_after_drained_restarting(tmp_path: Any) -> None:
    """AC6: cancel refused once kill is committed (drained_restarting)."""
    store = _store(tmp_path)
    intent = store.create_intent(
        service=_SERVICE, action="restart", deadline_at="d", reason="r"
    )
    store.advance(intent.intent_id, status=STATUS_DRAINED_RESTARTING)
    with pytest.raises(RestartIntentCancelError) as excinfo:
        store.cancel(intent.intent_id)
    assert excinfo.value.status == STATUS_DRAINED_RESTARTING
    assert store.get(intent.intent_id).status == STATUS_DRAINED_RESTARTING  # type: ignore[union-attr]


def test_orchestrate_cancel_pre_epoch_and_post_epoch_release(
    tmp_path: Any,
) -> None:
    """AC1/AC3: API orchestration cancel; post-epoch calls release before store cancel."""
    store = _store(tmp_path)
    pre = store.create_intent(
        service=_SERVICE, action="restart", deadline_at="d", reason="pre"
    )
    result_pre = _run(orchestrate_cancel_restart_intent(store, intent_id=pre.intent_id))
    assert result_pre["status"] == "cancelled"
    assert result_pre["drain_release"] is None
    assert store.get(pre.intent_id).status == STATUS_CANCELLED  # type: ignore[union-attr]

    post = store.create_intent(
        service=_SERVICE, action="restart", deadline_at="d", reason="post"
    )
    store.set_drain_epoch(
        post.intent_id, drain_epoch=9, worker_id="w1", worker_started_at="t1"
    )
    releases: list[tuple[str, int]] = []

    async def _release(iid: str, epoch: int) -> dict[str, Any]:
        releases.append((iid, epoch))
        return {"draining": False, "drain_epoch": epoch, "intent_id": None}

    result_post = _run(
        orchestrate_cancel_restart_intent(
            store,
            intent_id=post.intent_id,
            release_drain=_release,
        )
    )
    assert result_post["status"] == "cancelled"
    assert releases == [(post.intent_id, 9)]
    assert result_post["drain_release"]["draining"] is False
    assert store.get(post.intent_id).status == STATUS_CANCELLED  # type: ignore[union-attr]


def test_orchestrate_fail_closed_when_release_fails(tmp_path: Any) -> None:
    """Partial-orchestrate guard: release failure must not write cancelled."""
    store = _store(tmp_path)
    intent = store.create_intent(
        service=_SERVICE, action="restart", deadline_at="d", reason="r"
    )
    store.set_drain_epoch(
        intent.intent_id, drain_epoch=3, worker_id="w1", worker_started_at="t1"
    )

    async def _boom(_iid: str, _epoch: int) -> dict[str, Any]:
        raise RuntimeError("worker unreachable")

    result = _run(
        orchestrate_cancel_restart_intent(
            store, intent_id=intent.intent_id, release_drain=_boom
        )
    )
    assert result["status"] == "error"
    assert result["reason"] == "drain_release_failed"
    assert store.get(intent.intent_id).status == STATUS_PENDING_DRAIN  # type: ignore[union-attr]


def test_orchestrate_refuse_after_final_check_commit(tmp_path: Any) -> None:
    """AC6: manage cancel returns structured refuse after drained_restarting."""
    store = _store(tmp_path)
    intent = store.create_intent(
        service=_SERVICE, action="restart", deadline_at="d", reason="r"
    )
    store.advance(intent.intent_id, status=STATUS_DRAINED_RESTARTING)
    result = _run(orchestrate_cancel_restart_intent(store, intent_id=intent.intent_id))
    assert result["status"] == "refused"
    assert result["intent_status"] == STATUS_DRAINED_RESTARTING
    assert store.get(intent.intent_id).status == STATUS_DRAINED_RESTARTING  # type: ignore[union-attr]


def test_supervisor_aborts_on_cancel_mid_await(
    tmp_path: Any, events_log: list[tuple[str, dict[str, Any]]]
) -> None:
    """AC2: cancelled intent never reaches kill(); no completed-restart events."""
    store = _store(tmp_path)
    intent = store.create_intent(
        service=_SERVICE, action="restart", deadline_at="d", reason="r"
    )
    worker = _Worker(
        drain_states=[
            _snap(draining=False, epoch=0, active=1),
            _snap(draining=True, epoch=1, active=1),
        ],
        begin_snap=_snap(draining=True, epoch=1, active=1),
    )
    kill = _Kill()
    released: list[tuple[str, int]] = []

    async def _release(iid: str, epoch: int) -> dict[str, Any]:
        released.append((iid, epoch))
        return {"draining": False, "drain_epoch": epoch}

    sup = GitWorkerDrainSupervisor(
        store=store,
        begin_drain=worker.begin_drain,
        drain_state=worker.drain_state,
        subscribe_events=_Feed([]),
        kill=kill,
        cancel_drain=_release,
        deadline_s=5.0,
        reconcile_interval_s=0.01,
        progress_interval_s=999.0,
    )

    async def _run_and_cancel() -> None:
        task = asyncio.create_task(sup.supervise(intent))
        for _ in range(50):
            await asyncio.sleep(0.01)
            got = store.get(intent.intent_id)
            if got is not None and got.drain_epoch is not None:
                store.cancel(intent.intent_id)
                break
        await task

    _run(_run_and_cancel())

    assert kill.calls == 0
    got = store.get(intent.intent_id)
    assert got is not None and got.status == STATUS_CANCELLED
    assert got.status != STATUS_DRAINED_RESTARTING
    signals = [s for s, _ in events_log]
    assert "manage.restart.cancelled" in signals
    assert "manage.restart.completed" not in signals
    assert released == [(intent.intent_id, 1)]


def test_timeout_affordance_cites_cancel_restart_intent(
    tmp_path: Any, events_log: list[tuple[str, dict[str, Any]]]
) -> None:
    """AC5: timeout affordances must not retain cancel-if-supported residue."""
    store = _store(tmp_path)
    intent = store.create_intent(
        service=_SERVICE, action="stop", deadline_at="d", reason="r"
    )
    stuck = _snap(
        draining=True,
        epoch=1,
        active=1,
        ops=[{"op_id": "stuck-ticket"}],
    )
    worker = _Worker(
        drain_states=[stuck],
        begin_snap=stuck,
    )
    kill = _Kill()
    sup = _supervisor(
        store,
        worker,
        _Feed([_drain_completed(epoch=2, worker_id="w1")]),
        kill,
        deadline_s=0.05,
        stall_window_s=0.08,
    )

    _run(sup.supervise(intent))

    timeout_payloads = [p for s, p in events_log if s == "manage.restart.timeout"]
    assert timeout_payloads
    affordances = timeout_payloads[0]["affordances"]
    assert "cancel-if-supported" not in affordances
    assert any("cancel_restart_intent" in a for a in affordances)
    assert any(intent.intent_id in a for a in affordances)


def test_mcp_allowlist_accepts_cancel_restart_intent() -> None:
    """AC5/F: MCP manage allowlist includes cancel_restart_intent."""
    import ast
    from pathlib import Path

    manage_path = (
        Path(__file__).resolve().parents[5]
        / "services"
        / "mcp-server"
        / "tools"
        / "manage.py"
    )
    # parents: tests→controller→ui→model_manager→scripts→repo root
    tree = ast.parse(manage_path.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_VALID_ACTIONS":
                    # frozenset({...}) — extract string constants
                    call = node.value
                    assert isinstance(call, ast.Call)
                    elts = call.args[0].elts  # type: ignore[attr-defined]
                    names = {e.value for e in elts if isinstance(e, ast.Constant)}
                    assert "cancel_restart_intent" in names
                    assert "recycle_giw" in names
                    return
    raise AssertionError("_VALID_ACTIONS not found in manage.py")


def test_claim_kill_one_winner_per_generation(tmp_path: Any) -> None:
    store = _store(tmp_path)
    a = store.create_intent(
        service=_SERVICE, action="restart", deadline_at="d", reason="r1"
    )
    store.set_drain_epoch(
        a.intent_id, drain_epoch=1, worker_id="w1", worker_started_at="t1"
    )
    store.advance(a.intent_id, status=STATUS_TIMEOUT)
    b = store.create_intent(
        service=_SERVICE, action="restart", deadline_at="d", reason="r2"
    )
    assert b.intent_id != a.intent_id
    store.set_drain_epoch(
        b.intent_id, drain_epoch=1, worker_id="w1", worker_started_at="t1"
    )
    assert store.claim_kill(
        a.intent_id, worker_id="w1", worker_started_at="t1", drain_epoch=1
    )
    assert not store.claim_kill(
        b.intent_id, worker_id="w1", worker_started_at="t1", drain_epoch=1
    )
    assert store.get(a.intent_id).status == STATUS_DRAINED_RESTARTING
    assert store.get(b.intent_id).status == STATUS_PENDING_DRAIN


def test_idle_escalate_kills_without_drain_idle(
    tmp_path: Any, events_log: list[tuple[str, dict[str, Any]]]
) -> None:
    """Recycle mode: stable occupants with no heartbeat → force kill."""
    store = _store(tmp_path)
    intent = store.create_intent(
        service=_SERVICE, action="recycle_giw", deadline_at="d", reason="r"
    )
    stuck = _snap(
        draining=True,
        epoch=1,
        active=1,
        ops=[{"op_id": "job-stuck", "kind": "cursor-auto"}],
    )
    worker = _Worker(
        drain_states=[_snap(draining=False, epoch=0, active=1), stuck],
        begin_snap=stuck,
    )
    kill = _Kill()
    sup = _supervisor(
        store, worker, _Feed([]), kill, deadline_s=5.0, idle_escalate_s=0.05
    )
    _run(sup.supervise(intent))
    assert kill.calls == 1
    signals = [s for s, _ in events_log]
    assert "manage.recycle.escalated" in signals
    assert "manage.recycle.completed" in signals
    completed = [p for s, p in events_log if s == "manage.recycle.completed"]
    assert completed and completed[-1]["escalated"] is True


def test_idle_gate_does_not_fire_when_auto_heartbeat_fresh(
    tmp_path: Any, events_log: list[tuple[str, dict[str, Any]]]
) -> None:
    """Heartbeating Auto occupant must not force; drain timeout stays alert-only."""
    store = _store(tmp_path)
    intent = store.create_intent(
        service=_SERVICE, action="recycle_giw", deadline_at="d", reason="r"
    )
    busy = _snap(
        draining=True,
        epoch=1,
        active=1,
        ops=[{"op_id": "job-live", "kind": "cursor-auto"}],
    )

    async def _liveness() -> dict[str, Any]:
        return {"queue_health": {"occupant_idle_s": 1.0}}

    worker = _Worker(
        drain_states=[_snap(draining=False, epoch=0, active=1), busy],
        begin_snap=busy,
    )
    kill = _Kill()
    sup = _supervisor(
        store, worker, _Feed([]), kill, deadline_s=0.05, idle_escalate_s=0.2
    )
    sup.liveness_state = _liveness
    _run(sup.supervise(intent))
    assert kill.calls == 0
    signals = [s for s, _ in events_log]
    assert "manage.recycle.escalated" not in signals
    assert "manage.restart.timeout" in signals
