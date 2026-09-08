"""Recycle park-first + park_live step 1b edges (AC-SR-5 / AC-SR-6, steer-restart v1).

Companion to the two park tests in ``test_git_worker_drain_supervisor.py``:
this file covers the branches those leave open — a refused step-1b sweep keeps
the intent in keep-await (never ``failed``), a successful recycle park lets the
drain converge and restart *without* escalation, the park-first attempt cap
falls through to force, the restart-intent projection carries the park fields,
and the intent-store migration adds the columns to a pre-park database.
"""

from __future__ import annotations

import asyncio
import sqlite3
from typing import Any

import pytest

from scripts.model_manager.ui.controller.git_worker_drain_supervisor import (
    GitWorkerDrainSupervisor,
    build_git_worker_drain_supervisor,
)
from scripts.model_manager.ui.controller.restart_intent_consumer import (
    project_restart_intent_consumer,
)
from scripts.model_manager.ui.controller.restart_intent_migrate import (
    apply_restart_intent_schema,
)
from scripts.model_manager.ui.controller.restart_intent_store import (
    STATUS_ACTIVATION_UNVERIFIED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PENDING_DRAIN,
    STATUS_VERIFYING_ACTIVATION,
    RestartIntentStore,
)
from scripts.model_manager.ui.controller.tests.test_git_worker_drain_supervisor import (
    _drain_completed,
    _Feed,
    _Kill,
    _snap,
    _store,
    _supervise_until,
    _Worker,
)

_SERVICE = "git_integration_worker"


@pytest.fixture
def events_log(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    log: list[tuple[str, dict[str, Any]]] = []

    async def _fake_emit(signal: str, payload: dict[str, Any], **_kw: Any) -> None:
        log.append((signal, payload))

    monkeypatch.setattr("scripts.model_manager.observation_event._emit", _fake_emit)
    return log


def _sup(
    store: RestartIntentStore,
    worker: _Worker,
    feed: _Feed,
    kill: _Kill,
    *,
    park: Any,
    idle_escalate_s: float | None = None,
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
        idle_escalate_s=idle_escalate_s,
        park_for_restart=park,
    )


def test_step_1b_refusals_keep_await_never_fail(
    tmp_path: Any, events_log: list[tuple[str, dict[str, Any]]]
) -> None:
    """AC-SR-5: a refused sweep leaves the intent pending (keep-await), summary persisted."""
    store = _store(tmp_path)
    intent = store.create_intent(
        service=_SERVICE,
        action="sync_restart",
        deadline_at="d",
        reason="deploy",
        park_live=True,
    )
    busy = _snap(draining=True, epoch=1, active=1, ops=[{"op_id": "nested-child"}])
    worker = _Worker(
        drain_states=[_snap(draining=False, epoch=0, active=1), busy], begin_snap=busy
    )
    refused_summary = {
        "requested": [],
        "refused": [{"dispatch_id": "nested-child", "refusal": "NEST_CHAIN"}],
        "already_parked": [],
        "live_after": 1,
    }

    async def _park(_i: str, _e: int | None, _r: str) -> dict[str, Any]:
        return refused_summary

    kill = _Kill()
    sup = _sup(store, worker, _Feed([]), kill, park=_park)
    asyncio.run(_supervise_until(sup, intent, hold_s=0.3))

    assert kill.calls == 0
    stored = store.get(intent.intent_id)
    assert stored is not None
    assert stored.status == STATUS_PENDING_DRAIN
    assert stored.status != STATUS_FAILED
    assert stored.park_summary == refused_summary
    requested = [p for s, p in events_log if s == "manage.restart.park_live_requested"]
    assert requested and requested[0]["refused"][0]["refusal"] == "NEST_CHAIN"
    projection = project_restart_intent_consumer(stored)
    assert projection["park_live"] is True
    assert projection["park_summary"]["live_after"] == 1


def test_step_1b_transport_error_is_swallowed(
    tmp_path: Any, events_log: list[tuple[str, dict[str, Any]]]
) -> None:
    store = _store(tmp_path)
    intent = store.create_intent(
        service=_SERVICE, action="restart", deadline_at="d", reason="r", park_live=True
    )
    done = _snap(draining=True, epoch=1, active=0)
    worker = _Worker(
        drain_states=[_snap(draining=False, epoch=0), done], begin_snap=done
    )

    async def _boom(_i: str, _e: int | None, _r: str) -> dict[str, Any]:
        raise RuntimeError("worker unreachable")

    kill = _Kill()
    sup = _sup(store, worker, _Feed([_drain_completed(epoch=1)]), kill, park=_boom)
    asyncio.run(sup.supervise(intent))
    assert kill.calls == 1
    assert store.get(intent.intent_id).park_summary is None  # type: ignore[union-attr]
    assert "manage.restart.park_live_requested" not in [s for s, _ in events_log]


def test_recycle_park_success_converges_without_escalation(
    tmp_path: Any, events_log: list[tuple[str, dict[str, Any]]]
) -> None:
    """AC-SR-6: idle occupant parked → drain converges → restart with escalated=False."""
    store = _store(tmp_path)
    intent = store.create_intent(
        service=_SERVICE, action="recycle_giw", deadline_at="d", reason="recycle"
    )
    stuck = _snap(
        draining=True,
        epoch=1,
        active=1,
        ops=[{"op_id": "judgment-dispatch", "kind": "cursor_sdk"}],
    )
    idle = _snap(draining=True, epoch=1, active=0)
    parked: list[str] = []

    class _ParkThenIdleWorker(_Worker):
        async def drain_state(self) -> dict[str, Any]:
            return idle if parked else stuck

    worker = _ParkThenIdleWorker(drain_states=[stuck], begin_snap=stuck)

    async def _park(intent_id: str, epoch: int | None, reason: str) -> dict[str, Any]:
        parked.append(intent_id)
        return {
            "requested": ["judgment-dispatch"],
            "refused": [],
            "already_parked": [],
            "live_after": 0,
        }

    kill = _Kill()
    sup = _sup(store, worker, _Feed([]), kill, park=_park, idle_escalate_s=0.05)
    asyncio.run(sup.supervise(intent))

    assert parked == [intent.intent_id]
    assert kill.calls == 1
    signals = [s for s, _ in events_log]
    assert "manage.recycle.escalated" not in signals
    completed = [p for s, p in events_log if s == "manage.recycle.completed"]
    assert completed and completed[-1]["escalated"] is False
    stored = store.get(intent.intent_id)
    assert stored is not None and stored.status in {
        STATUS_COMPLETED,
        STATUS_VERIFYING_ACTIVATION,
        STATUS_ACTIVATION_UNVERIFIED,
    }
    assert stored.park_summary == {
        "requested": ["judgment-dispatch"],
        "refused": [],
        "already_parked": [],
        "live_after": 0,
    }


def test_recycle_park_attempt_cap_falls_through_to_force(
    tmp_path: Any, events_log: list[tuple[str, dict[str, Any]]]
) -> None:
    store = _store(tmp_path)
    intent = store.create_intent(
        service=_SERVICE, action="recycle_giw", deadline_at="d", reason="recycle"
    )
    stuck = _snap(
        draining=True, epoch=1, active=1, ops=[{"op_id": "ghost", "kind": "cursor_sdk"}]
    )
    worker = _Worker(
        drain_states=[_snap(draining=False, epoch=0, active=1), stuck], begin_snap=stuck
    )
    calls = 0

    async def _park(_i: str, _e: int | None, _r: str) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        # Retryable refusal only: never a hard refusal, never freed.
        return {
            "requested": [],
            "refused": [{"dispatch_id": "ghost", "refusal": "NOT_RESUMABLE_YET"}],
            "already_parked": [],
            "live_after": 1,
        }

    kill = _Kill()
    sup = _sup(store, worker, _Feed([]), kill, park=_park, idle_escalate_s=0.03)
    asyncio.run(sup.supervise(intent))
    assert calls == 3  # _PARK_IDLE_MAX_ATTEMPTS
    assert kill.calls == 1
    escalated = [p for s, p in events_log if s == "manage.recycle.escalated"]
    assert escalated and escalated[-1]["park_attempted"] is True
    assert escalated[-1]["park_refusals"][0]["refusal"] == "NOT_RESUMABLE_YET"


def test_factory_wires_park_transport_by_default(tmp_path: Any) -> None:
    store = _store(tmp_path)

    async def _kill() -> str:
        return "stopped"

    default = build_git_worker_drain_supervisor(
        store,
        worker_url="http://127.0.0.1:1",
        events_query_socket="/nonexistent",
        kill=_kill,
    )
    assert default.park_for_restart is not None
    legacy = build_git_worker_drain_supervisor(
        store,
        worker_url="http://127.0.0.1:1",
        events_query_socket="/nonexistent",
        kill=_kill,
        park_first=False,
    )
    assert legacy.park_for_restart is None


def test_migration_adds_park_columns_to_pre_park_db(tmp_path: Any) -> None:
    path = tmp_path / "old-intents.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE restart_intents (
            intent_id TEXT PRIMARY KEY, service TEXT NOT NULL,
            action TEXT NOT NULL DEFAULT 'restart',
            status TEXT NOT NULL CHECK (status IN (
                'pending_drain','drained_restarting','verifying_activation',
                'completed','activation_unverified',
                'failed','timeout','force_requested','cancelled')),
            drain_epoch INTEGER, worker_id TEXT, worker_started_at TEXT, deadline_at TEXT,
            last_seen_event_seq INTEGER NOT NULL DEFAULT 0, reason TEXT, kill_boundary_at TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        INSERT INTO restart_intents (intent_id, service, status, created_at, updated_at)
        VALUES ('old-1', 'git_integration_worker', 'completed', 'c', 'u');
        """
    )
    conn.commit()
    apply_restart_intent_schema(conn)
    conn.commit()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(restart_intents)")}
    assert {"park_live", "park_summary"} <= cols
    conn.close()
    store = RestartIntentStore(db_path=path)
    old = store.get("old-1")
    assert old is not None and old.park_live is False and old.park_summary is None
    fresh = store.create_intent(
        service="stargate",
        action="restart",
        deadline_at="d",
        reason="r",
        park_live=True,
    )
    assert fresh.park_live is True
    store.set_park_summary(
        fresh.intent_id, summary={"requested": ["x"], "live_after": 0}
    )
    assert store.get(fresh.intent_id).park_summary == {
        "requested": ["x"],
        "live_after": 0,
    }  # type: ignore[union-attr]
