"""Expiry cancels through drain release. Reads do not write timeout."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from scripts.model_manager.ui.controller.restart_intent_expiry import (
    apply_intent_to_busy_entry,
    expire_via_cancel,
)
from scripts.model_manager.ui.controller.restart_intent_migrate import (
    INTENT_EXPIRY_WINDOW_S,
    apply_restart_intent_schema,
)
from scripts.model_manager.ui.controller.restart_intent_store import RestartIntentStore


@pytest.mark.asyncio
async def test_expiry_releases_drain_then_cancels(tmp_path) -> None:
    """Past the window, expiry releases the drain and cancels. It does not
    write status=timeout (cancel would then refuse and GIW stays draining).
    """
    store = RestartIntentStore(tmp_path / "restart-intents.db")
    intent = store.create_intent(
        service="git_integration_worker",
        action="restart",
        deadline_at="ceiling",
        reason="armed",
        caller_agent="cursor-sdk",
    )
    store.set_drain_epoch(
        intent.intent_id,
        drain_epoch=3,
        worker_id="w",
        worker_started_at="t0",
    )
    past = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
    store._update(intent.intent_id, expires_at=past)
    released: list[tuple[str, int]] = []

    async def release(intent_id: str, drain_epoch: int) -> dict[str, int]:
        released.append((intent_id, drain_epoch))
        return {"released": True}

    assert await expire_via_cancel(store, intent.intent_id, release_drain=release)
    assert released == [(intent.intent_id, 3)]
    stored = store.get(intent.intent_id)
    assert stored is not None
    assert stored.status == "cancelled"


def test_readonly_busy_check_does_not_write_timeout(tmp_path) -> None:
    """A busy projection of an expired arm does not change the row."""
    store = RestartIntentStore(tmp_path / "restart-intents.db")
    intent = store.create_intent(
        service="git_integration_worker",
        action="restart",
        deadline_at="ceiling",
        reason="armed",
        caller_agent="cursor-sdk",
    )
    past = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
    store._update(intent.intent_id, expires_at=past)
    expired = store.get(intent.intent_id)
    assert expired is not None
    entry = {"busy": False, "restart_would_defer": True, "determination": "idle"}
    apply_intent_to_busy_entry(entry, expired)
    assert entry["restart_intent"] is None
    assert entry["restart_would_defer"] is False
    again = store.get(intent.intent_id)
    assert again is not None
    assert again.status == "pending_drain"
    assert store.pending_intents()[0].status == "pending_drain"


def test_caller_agent_survives_joining_request(tmp_path) -> None:
    store = RestartIntentStore(tmp_path / "restart-intents.db")
    first = store.create_intent(
        service="git_integration_worker",
        action="restart",
        deadline_at="ceiling",
        reason="armed",
        caller_agent="cursor-sdk",
    )
    joined = store.create_intent(
        service="git_integration_worker",
        action="restart",
        deadline_at="ceiling",
        reason="join",
        caller_agent="other-seat",
    )
    assert joined.intent_id == first.intent_id
    assert joined.caller_agent == "cursor-sdk"
    assert joined.expires_at is not None
    assert first.expires_at is not None
    assert joined.expires_at >= first.expires_at


@pytest.mark.asyncio
async def test_abandoned_intent_30s_tick_expires(tmp_path) -> None:
    """The supervisor progress tick (30s) expires; it does not re-arm."""
    from scripts.model_manager.ui.controller.git_worker_drain_supervisor import (
        GitWorkerDrainSupervisor,
    )

    store = RestartIntentStore(tmp_path / "restart-intents.db")
    intent = store.create_intent(
        service="git_integration_worker",
        action="restart",
        deadline_at="ceiling",
        reason="abandoned",
        caller_agent="cursor-sdk",
    )
    store.set_drain_epoch(
        intent.intent_id,
        drain_epoch=1,
        worker_id="w",
        worker_started_at="t0",
    )
    past = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
    store._update(intent.intent_id, expires_at=past)
    released: list[tuple[str, int]] = []

    async def release(intent_id: str, epoch: int) -> None:
        released.append((intent_id, epoch))

    async def _noop(*_a: object, **_k: object) -> dict[str, str]:
        return {}

    supervisor = GitWorkerDrainSupervisor(
        store=store,
        begin_drain=_noop,
        drain_state=_noop,
        subscribe_events=_noop,  # type: ignore[arg-type]
        kill=_noop,
        cancel_drain=release,
        progress_interval_s=30.0,
    )
    assert supervisor.progress_interval_s == 30.0
    assert await supervisor._expire_abandoned(intent)
    assert released == [(intent.intent_id, 1)]
    stored = store.get(intent.intent_id)
    assert stored is not None
    assert stored.status == "cancelled"


@pytest.mark.asyncio
async def test_api_dispatch_arm_stores_request_caller_and_busy_status_projects_it(
    tmp_path,
) -> None:
    """A manage request, not a direct store insert, arms a non-unknown caller.

    Breaks when api_dispatch omits caller_agent and arm_stamps writes unknown.
    Join then cannot correct it. busy_status must project the stored caller.
    """
    import asyncio
    from unittest.mock import MagicMock

    from scripts.model_manager.ui import api_dispatch
    from scripts.model_manager.ui.controller.restart_drain import RestartDrainGate

    store = RestartIntentStore(tmp_path / "restart-intents.db")
    gate = RestartDrainGate(probes={})

    class _Supervisor:
        deadline_s = 30.0

        def __init__(self) -> None:
            self._block = asyncio.Event()

        async def supervise(self, _intent: object) -> None:
            await self._block.wait()

    supervisor = _Supervisor()
    ctl = MagicMock()
    ctl.restart_intent_store = store
    ctl.restart_gate = gate
    ctl.build_git_worker_drain_supervisor.return_value = supervisor
    ctl.git_worker_kill_for.return_value = supervisor

    result = await api_dispatch.execute(
        ctl,
        "sync_restart",
        "git_integration_worker",
        {"caller_agent": "cursor-sdk"},
    )
    supervisor._block.set()
    await asyncio.sleep(0)
    assert result["status"] == "deferred"
    stored = store.active_for_service("git_integration_worker")
    assert stored is not None
    assert stored.caller_agent == "cursor-sdk"
    assert stored.caller_agent != "unknown"

    status = await api_dispatch._busy_status(ctl, service="git_integration_worker")
    projected = status["restart_intent"]
    assert projected["caller_agent"] == "cursor-sdk"
    assert projected["armed_at"]
    assert projected["expires_at"]


@pytest.mark.asyncio
async def test_expiry_release_failure_stays_pending_and_next_tick_cancels(
    tmp_path, monkeypatch
) -> None:
    """Cancel-drain raising on the 30s tick must not mark the intent failed.

    GIW stays draining and cancel_restart_intent can still release. The next
    tick's successful release cancels the row.
    """
    import time

    from scripts.model_manager.ui.api_dispatch import orchestrate_cancel_restart_intent
    from scripts.model_manager.ui.controller.git_worker_drain_supervisor import (
        GitWorkerDrainSupervisor,
    )

    store = RestartIntentStore(tmp_path / "restart-intents.db")
    intent = store.create_intent(
        service="git_integration_worker",
        action="restart",
        deadline_at="ceiling",
        reason="abandoned",
        caller_agent="manage",
    )
    store.set_drain_epoch(
        intent.intent_id,
        drain_epoch=4,
        worker_id="w",
        worker_started_at="t0",
    )
    past = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
    store._update(intent.intent_id, expires_at=past)

    async def _emit(*_a, **_k) -> None:
        return None

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.git_worker_drain_supervisor.events.emit_manage_restart_draining",
        _emit,
    )

    calls = {"n": 0}

    async def release(intent_id: str, epoch: int) -> dict[str, bool]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("cancel-drain 503")
        return {"released": True}

    async def drain_state() -> dict[str, object]:
        return {
            "draining": True,
            "active_count": 1,
            "drain_epoch": 4,
            "worker_id": "w",
            "worker_started_at": "t0",
        }

    def subscribe(_seq: int):
        raise RuntimeError("no subscription")

    async def _noop(*_a, **_k) -> dict[str, str]:
        return {}

    supervisor = GitWorkerDrainSupervisor(
        store=store,
        begin_drain=_noop,
        drain_state=drain_state,
        subscribe_events=subscribe,
        kill=_noop,
        cancel_drain=release,
        progress_interval_s=0.0,
        reconcile_interval_s=0.0,
        deadline_s=60.0,
    )
    outcome, _start = await supervisor._await_drain_completed(
        store.get(intent.intent_id), time.monotonic() + 60.0, time.monotonic()
    )
    assert outcome == "cancelled"
    assert calls["n"] >= 2
    stored = store.get(intent.intent_id)
    assert stored is not None
    assert stored.status == "cancelled"

    # A release that raises, observed before the retry, stays pending and
    # cancel_restart_intent can still release.
    again = store.create_intent(
        service="stargate",
        action="restart",
        deadline_at="ceiling",
        reason="armed",
        caller_agent="manage",
    )
    store.set_drain_epoch(
        again.intent_id,
        drain_epoch=2,
        worker_id="w2",
        worker_started_at="t1",
    )
    store._update(again.intent_id, expires_at=past)
    released: list[tuple[str, int]] = []

    async def boom(_intent_id: str, _epoch: int) -> None:
        raise RuntimeError("cancel-drain down")

    assert not await expire_via_cancel(store, again.intent_id, release_drain=boom)
    mid = store.get(again.intent_id)
    assert mid is not None
    assert mid.status == "pending_drain"

    async def ok(intent_id: str, epoch: int) -> dict[str, bool]:
        released.append((intent_id, epoch))
        return {"released": True}

    cancelled = await orchestrate_cancel_restart_intent(
        store, intent_id=again.intent_id, release_drain=ok
    )
    assert cancelled["status"] == "cancelled"
    assert released == [(again.intent_id, 2)]


def test_migration_gives_inflight_row_a_fresh_window(tmp_path) -> None:
    """Backfill dates the window from now, not created_at."""
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE restart_intents (
            intent_id TEXT PRIMARY KEY,
            service TEXT NOT NULL,
            action TEXT NOT NULL,
            status TEXT NOT NULL,
            drain_epoch INTEGER,
            worker_id TEXT,
            worker_started_at TEXT,
            deadline_at TEXT,
            last_seen_event_seq INTEGER NOT NULL DEFAULT 0,
            reason TEXT,
            kill_boundary_at TEXT,
            park_live INTEGER NOT NULL DEFAULT 0,
            park_summary TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    created = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    conn.execute(
        "INSERT INTO restart_intents "
        "(intent_id, service, action, status, last_seen_event_seq, created_at, updated_at) "
        "VALUES ('old', 'git_integration_worker', 'restart', 'pending_drain', 0, ?, ?)",
        (created, created),
    )
    conn.commit()
    before = datetime.now(UTC)
    apply_restart_intent_schema(conn)
    conn.commit()
    row = conn.execute(
        "SELECT expires_at, created_at FROM restart_intents WHERE intent_id='old'"
    ).fetchone()
    conn.close()
    assert row[0] is not None
    expires = datetime.fromisoformat(row[0])
    assert expires > before
    assert expires - before >= timedelta(seconds=INTENT_EXPIRY_WINDOW_S - 5)
