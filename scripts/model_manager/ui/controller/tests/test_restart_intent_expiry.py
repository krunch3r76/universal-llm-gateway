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
