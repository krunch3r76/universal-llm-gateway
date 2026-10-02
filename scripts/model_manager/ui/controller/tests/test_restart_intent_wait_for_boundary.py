"""a:37197 — wait_for_boundary arm: deferred begin_drain + optional TTL."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

import pytest

from scripts.model_manager.ui.api_dispatch import (
    giw_intent_ttl_s_from_params,
    giw_wait_for_boundary_from_params,
)
from scripts.model_manager.ui.controller.git_worker_drain_supervisor import (
    GitWorkerDrainSupervisor,
)
from scripts.model_manager.ui.controller.restart_intent_consumer import (
    drain_deferred_result,
)
from scripts.model_manager.ui.controller.restart_intent_expiry import (
    expire_via_cancel,
    intent_expired,
    resolve_intent_ttl_s,
)
from scripts.model_manager.ui.controller.restart_intent_migrate import (
    INTENT_EXPIRY_WINDOW_S,
    apply_restart_intent_schema,
)
from scripts.model_manager.ui.controller.restart_intent_store import RestartIntentStore


class _Worker:
    def __init__(self, states: list[dict[str, Any]], begin: dict[str, Any]) -> None:
        self._states = list(states)
        self._begin = begin
        self.begun: list[dict[str, Any]] = []

    async def drain_state(self) -> dict[str, Any]:
        if len(self._states) > 1:
            return self._states.pop(0)
        return self._states[0]

    async def begin_drain(self, body: dict[str, Any]) -> dict[str, Any]:
        self.begun.append(body)
        return self._begin


class _Feed:
    def __call__(self, _seq: int):
        async def _agen():
            if False:  # pragma: no cover — empty async iter
                yield {}
            return

        return _agen()


class _Kill:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self) -> str:
        self.calls += 1
        return "stopped"


def _supervisor(
    store: RestartIntentStore,
    worker: _Worker,
    *,
    progress_interval_s: float = 999.0,
) -> GitWorkerDrainSupervisor:
    return GitWorkerDrainSupervisor(
        store=store,
        begin_drain=worker.begin_drain,
        drain_state=worker.drain_state,
        subscribe_events=_Feed(),
        kill=_Kill(),
        deadline_s=5.0,
        reconcile_interval_s=0.01,
        progress_interval_s=progress_interval_s,
        park_live_grace_s=0.0,
    )


async def _supervise_until(
    sup: GitWorkerDrainSupervisor,
    intent: Any,
    *,
    done: Callable[[], bool] | None = None,
    hold_s: float = 1.0,
) -> None:
    task = asyncio.create_task(sup.supervise(intent))
    try:
        deadline = time.monotonic() + hold_s
        while time.monotonic() < deadline:
            if done is not None and done():
                break
            await asyncio.sleep(0.01)
        if done is not None and not done():
            raise AssertionError(f"condition not met within {hold_s}s")
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        elif task.exception() is not None:
            raise task.exception()  # type: ignore[misc]


def test_resolve_ttl_wait_for_boundary_defaults_unbounded() -> None:
    assert resolve_intent_ttl_s(wait_for_boundary=True, intent_ttl_s=None) is None
    assert (
        resolve_intent_ttl_s(wait_for_boundary=False, intent_ttl_s=None)
        == INTENT_EXPIRY_WINDOW_S
    )
    assert resolve_intent_ttl_s(wait_for_boundary=True, intent_ttl_s=120.0) == 120.0


def test_create_intent_wait_for_boundary_no_expires(tmp_path) -> None:
    store = RestartIntentStore(tmp_path / "restart-intents.db")
    intent = store.create_intent(
        service="git_integration_worker",
        action="sync_restart",
        deadline_at="ceiling",
        reason="a37197",
        wait_for_boundary=True,
        park_live=False,
        caller_agent="cursor",
    )
    assert intent.wait_for_boundary is True
    assert intent.expires_at is None
    assert not intent_expired(intent)
    env = drain_deferred_result(intent)
    assert env["state"] == "waiting_for_boundary"
    assert env["drain_begun"] is False


def test_create_intent_caller_ttl_on_wait_mode(tmp_path) -> None:
    store = RestartIntentStore(tmp_path / "restart-intents.db")
    before = datetime.now(UTC)
    intent = store.create_intent(
        service="git_integration_worker",
        action="sync_restart",
        deadline_at="ceiling",
        reason="ttl",
        wait_for_boundary=True,
        intent_ttl_s=90.0,
        caller_agent="cursor",
    )
    assert intent.expires_at is not None
    expires = datetime.fromisoformat(intent.expires_at)
    assert expires - before >= timedelta(seconds=85)


def test_schema_reopen_preserves_null_expires_for_wait_mode(tmp_path) -> None:
    """Service-down / store reopen must not stamp 600s onto wait_for_boundary nulls."""
    db = tmp_path / "restart-intents.db"
    store = RestartIntentStore(db)
    intent = store.create_intent(
        service="git_integration_worker",
        action="sync_restart",
        deadline_at="ceiling",
        reason="persist",
        wait_for_boundary=True,
        caller_agent="cursor",
    )
    assert intent.expires_at is None
    # Simulate manage process recycle: new store + schema apply.
    again = RestartIntentStore(db)
    with again._connect() as conn:
        apply_restart_intent_schema(conn)
        conn.commit()
    stored = again.get(intent.intent_id)
    assert stored is not None
    assert stored.wait_for_boundary is True
    assert stored.expires_at is None


def test_param_helpers() -> None:
    assert giw_wait_for_boundary_from_params({}) is False
    assert giw_wait_for_boundary_from_params({"wait_for_boundary": True}) is True
    assert giw_intent_ttl_s_from_params({}) is None
    assert giw_intent_ttl_s_from_params({"intent_ttl_s": 45}) == 45.0


@pytest.mark.asyncio
async def test_wait_for_boundary_defers_begin_drain_until_idle(tmp_path) -> None:
    """Concurrency / ROW_HOP: begin_drain only after active_count hits 0."""
    store = RestartIntentStore(tmp_path / "restart-intents.db")
    intent = store.create_intent(
        service="git_integration_worker",
        action="sync_restart",
        deadline_at="ceiling",
        reason="boundary",
        wait_for_boundary=True,
        park_live=False,
        caller_agent="cursor",
    )
    begin = {
        "drain_epoch": 1,
        "worker_id": "w",
        "worker_started_at": "t0",
        "active_count": 0,
        "draining": True,
    }
    worker = _Worker(
        [
            {"active_count": 1, "draining": False, "drain_epoch": 0},
            {"active_count": 1, "draining": False, "drain_epoch": 0},
            {"active_count": 0, "draining": False, "drain_epoch": 0},
            {
                "active_count": 0,
                "draining": True,
                "drain_epoch": 1,
                "worker_id": "w",
                "worker_started_at": "t0",
            },
        ],
        begin,
    )
    sup = _supervisor(store, worker)
    await _supervise_until(sup, intent, done=lambda: bool(worker.begun), hold_s=2.0)
    assert worker.begun, "begin_drain must fire after idle"
    stored = store.get(intent.intent_id)
    assert stored is not None
    assert stored.drain_epoch == 1


@pytest.mark.asyncio
async def test_wait_for_boundary_no_begin_while_busy(tmp_path) -> None:
    store = RestartIntentStore(tmp_path / "restart-intents.db")
    intent = store.create_intent(
        service="git_integration_worker",
        action="sync_restart",
        deadline_at="ceiling",
        reason="still-busy",
        wait_for_boundary=True,
        caller_agent="cursor",
    )
    worker = _Worker(
        [{"active_count": 2, "draining": False, "drain_epoch": 0}],
        {"drain_epoch": 1, "worker_id": "w", "worker_started_at": "t0"},
    )
    sup = _supervisor(store, worker)
    await _supervise_until(sup, intent, hold_s=0.2)
    assert worker.begun == []


@pytest.mark.asyncio
async def test_wait_for_boundary_caller_ttl_expires_without_begin(tmp_path) -> None:
    """Wrong ordering / abandon: TTL cancel before boundary releases without begin."""
    store = RestartIntentStore(tmp_path / "restart-intents.db")
    intent = store.create_intent(
        service="git_integration_worker",
        action="sync_restart",
        deadline_at="ceiling",
        reason="ttl-expire",
        wait_for_boundary=True,
        intent_ttl_s=0.05,
        caller_agent="cursor",
    )
    # Force past expiry immediately for the tick path.
    past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    store._update(intent.intent_id, expires_at=past)
    intent = store.get(intent.intent_id)
    assert intent is not None

    worker = _Worker(
        [{"active_count": 1, "draining": False, "drain_epoch": 0}],
        {"drain_epoch": 1, "worker_id": "w", "worker_started_at": "t0"},
    )
    sup = _supervisor(store, worker, progress_interval_s=0.01)
    await _supervise_until(sup, intent, hold_s=0.5)
    assert worker.begun == []
    stored = store.get(intent.intent_id)
    assert stored is not None
    assert stored.status == "cancelled"


@pytest.mark.asyncio
async def test_partial_drain_release_failure_leaves_pending(tmp_path) -> None:
    """Partial failure: expiry release boom leaves pending_drain (existing contract)."""
    store = RestartIntentStore(tmp_path / "restart-intents.db")
    intent = store.create_intent(
        service="git_integration_worker",
        action="sync_restart",
        deadline_at="ceiling",
        reason="partial",
        wait_for_boundary=False,
        caller_agent="cursor",
    )
    store.set_drain_epoch(
        intent.intent_id,
        drain_epoch=2,
        worker_id="w",
        worker_started_at="t0",
    )
    past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    store._update(intent.intent_id, expires_at=past)

    async def boom(_iid: str, _epoch: int) -> dict[str, Any]:
        raise RuntimeError("release failed")

    assert not await expire_via_cancel(
        store, intent.intent_id, release_drain=boom
    )
    stored = store.get(intent.intent_id)
    assert stored is not None
    assert stored.status == "pending_drain"
