"""a:37197 — wait_for_boundary arm: deferred begin_drain + optional TTL."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

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
    assert giw_intent_ttl_s_from_params({"intent_ttl_s": 0}) is None
    assert giw_intent_ttl_s_from_params({"intent_ttl_s": -1}) is None


def test_join_and_rearm_preserve_wait_mode_caller_ttl(tmp_path) -> None:
    """Wrong ordering: a TTL-less join/rearm must not wipe wait_for_boundary expires_at."""
    store = RestartIntentStore(tmp_path / "restart-intents.db")
    first = store.create_intent(
        service="git_integration_worker",
        action="sync_restart",
        deadline_at="ceiling",
        reason="ttl",
        wait_for_boundary=True,
        intent_ttl_s=90.0,
        caller_agent="cursor",
    )
    assert first.expires_at is not None
    joined = store.create_intent(
        service="git_integration_worker",
        action="sync_restart",
        deadline_at="ceiling",
        reason="join",
        wait_for_boundary=True,
        caller_agent="other",
    )
    assert joined.intent_id == first.intent_id
    assert joined.expires_at == first.expires_at
    rearms = store.rearm(first.intent_id)
    assert rearms.expires_at == first.expires_at


@pytest.mark.asyncio
async def test_wait_for_boundary_begin_drain_passes_holder_arm(tmp_path) -> None:
    """Sole active op → begin_drain fires immediately with arm=holder:<id>."""
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
        "active_count": 1,
        "draining": False,
        "armed": True,
        "arm": "holder:sole-1",
    }
    worker = _Worker(
        [
            {
                "active_count": 1,
                "draining": False,
                "drain_epoch": 0,
                "active_ops": [{"op_id": "sole-1", "kind": "cursor_sdk"}],
            },
        ],
        begin,
    )
    sup = _supervisor(store, worker)
    await _supervise_until(sup, intent, done=lambda: bool(worker.begun), hold_s=2.0)
    assert worker.begun, "begin_drain must fire immediately when armed"
    assert worker.begun[0].get("arm") == "holder:sole-1"
    stored = store.get(intent.intent_id)
    assert stored is not None
    assert stored.drain_epoch == 1


@pytest.mark.asyncio
async def test_wait_for_boundary_multi_busy_arms_idle(tmp_path) -> None:
    """Overlapping occupants → arm=idle (admits open until fleet idle)."""
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
        [
            {
                "active_count": 2,
                "draining": False,
                "drain_epoch": 0,
                "active_ops": [
                    {"op_id": "a", "kind": "cursor_sdk"},
                    {"op_id": "b", "kind": "cursor_sdk"},
                ],
            }
        ],
        {
            "drain_epoch": 1,
            "worker_id": "w",
            "worker_started_at": "t0",
            "armed": True,
            "arm": "idle",
            "draining": False,
        },
    )
    sup = _supervisor(store, worker)
    await _supervise_until(sup, intent, done=lambda: bool(worker.begun), hold_s=1.0)
    assert len(worker.begun) == 1
    assert worker.begun[0].get("arm") == "idle"


@pytest.mark.asyncio
async def test_wait_for_boundary_caller_ttl_expires_after_arm(tmp_path) -> None:
    """Caller TTL cancels via expire_via_cancel even when an arm was posted."""
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
    past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    store._update(intent.intent_id, expires_at=past)
    store.set_drain_epoch(
        intent.intent_id,
        drain_epoch=1,
        worker_id="w",
        worker_started_at="t0",
    )
    released: list[tuple[str, int]] = []

    async def _release(iid: str, epoch: int) -> dict[str, Any]:
        released.append((iid, epoch))
        return {"draining": False, "armed": False}

    assert await expire_via_cancel(store, intent.intent_id, release_drain=_release)
    assert released == [(intent.intent_id, 1)]
    stored = store.get(intent.intent_id)
    assert stored is not None
    assert stored.status == "cancelled"


def test_resolve_drain_arm_helpers() -> None:
    from scripts.model_manager.ui.controller.restart_intent_boundary_wait import (
        resolve_drain_arm,
    )

    assert resolve_drain_arm(None) == "idle"
    assert resolve_drain_arm({"active_ops": []}) == "idle"
    assert resolve_drain_arm({"active_ops": [{"op_id": "x"}]}) == "holder:x"
    assert resolve_drain_arm({"active_ops": [{"op_id": "a"}, {"op_id": "b"}]}) == "idle"


@pytest.mark.asyncio
async def test_wait_for_boundary_deadline_defers_until_draining(tmp_path) -> None:
    """AC5: armed-open-admits must not burn the manage drain deadline.

    Breaks if deferral is omitted: a past deadline while ``armed`` would
    immediately return timeout (prod GIT_WORKER_DRAIN_DEADLINE_S=600 under a
    long conductor holder).
    """
    store = RestartIntentStore(tmp_path / "restart-intents.db")
    intent = store.create_intent(
        service="git_integration_worker",
        action="sync_restart",
        deadline_at="ceiling",
        reason="ac5-defer",
        wait_for_boundary=True,
        park_live=False,
        caller_agent="cursor",
    )
    store.set_drain_epoch(
        intent.intent_id,
        drain_epoch=1,
        worker_id="w",
        worker_started_at="t0",
    )
    phase = {"n": 0}

    async def drain_state() -> dict[str, Any]:
        phase["n"] += 1
        if phase["n"] < 4:
            return {
                "draining": False,
                "armed": True,
                "arm": "holder:sole-1",
                "active_count": 1,
                "drain_epoch": 1,
                "worker_id": "w",
                "worker_started_at": "t0",
                "active_ops": [{"op_id": "sole-1"}],
            }
        return {
            "draining": True,
            "armed": False,
            "active_count": 0,
            "drain_epoch": 1,
            "worker_id": "w",
            "worker_started_at": "t0",
            "active_ops": [],
        }

    def subscribe(_seq: int):
        raise RuntimeError("no subscription")

    async def _noop(*_a, **_k) -> dict[str, Any]:
        return {}

    supervisor = GitWorkerDrainSupervisor(
        store=store,
        begin_drain=_noop,
        drain_state=drain_state,
        subscribe_events=subscribe,
        kill=_noop,
        deadline_s=0.05,
        reconcile_interval_s=0.01,
        progress_interval_s=999.0,
    )
    past_deadline = time.monotonic() - 10.0
    t_before = time.monotonic()
    outcome, effective_start = await supervisor._await_drain_completed(
        store.get(intent.intent_id),
        past_deadline,
        t_before,
        defer_deadline_until_draining=True,
    )
    assert outcome == "converged"
    assert effective_start >= t_before
    assert phase["n"] >= 4


@pytest.mark.asyncio
async def test_wait_for_boundary_deadline_starts_at_activate(tmp_path) -> None:
    """AC2: after activate with work still open, timeout fires ~deadline_s later.

    Breaks if the new-deadline line is dropped: outer past deadline would
    trip immediately once draining=True without resetting the clock.
    """
    store = RestartIntentStore(tmp_path / "restart-intents.db")
    intent = store.create_intent(
        service="git_integration_worker",
        action="sync_restart",
        deadline_at="ceiling",
        reason="ac2",
        wait_for_boundary=True,
        park_live=False,
        caller_agent="cursor",
    )
    store.set_drain_epoch(
        intent.intent_id,
        drain_epoch=1,
        worker_id="w",
        worker_started_at="t0",
    )
    phase = {"n": 0}
    base = {
        "drain_epoch": 1,
        "worker_id": "w",
        "worker_started_at": "t0",
        "active_count": 1,
        "active_ops": [{"op_id": "other"}],
    }

    async def drain_state() -> dict[str, Any]:
        phase["n"] += 1
        return {**base, "draining": phase["n"] >= 3, "armed": phase["n"] < 3}

    def subscribe(_seq: int):
        raise RuntimeError("no subscription")

    async def _noop(*_a, **_k) -> dict[str, Any]:
        return {}

    supervisor = GitWorkerDrainSupervisor(
        store=store,
        begin_drain=_noop,
        drain_state=drain_state,
        subscribe_events=subscribe,
        kill=_noop,
        deadline_s=0.2,
        reconcile_interval_s=0.01,
        progress_interval_s=999.0,
    )
    t_before = time.monotonic()
    outcome, eff = await supervisor._await_drain_completed(
        store.get(intent.intent_id),
        time.monotonic() - 10.0,
        t_before,
        defer_deadline_until_draining=True,
    )
    assert outcome == "timeout"
    assert eff > t_before
    assert time.monotonic() - eff >= 0.2


@pytest.mark.asyncio
async def test_wait_for_boundary_cancel_observed_while_armed(tmp_path) -> None:
    """AC3: cancel during the armed window returns cancelled under deferral."""
    store = RestartIntentStore(tmp_path / "restart-intents.db")
    intent = store.create_intent(
        service="git_integration_worker",
        action="sync_restart",
        deadline_at="ceiling",
        reason="ac3",
        wait_for_boundary=True,
        park_live=False,
        caller_agent="cursor",
    )
    store.set_drain_epoch(
        intent.intent_id,
        drain_epoch=1,
        worker_id="w",
        worker_started_at="t0",
    )
    calls = {"n": 0}

    async def drain_state() -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 3:
            store.advance(intent.intent_id, status="cancelled", reason="test")
        return {
            "draining": False,
            "armed": True,
            "drain_epoch": 1,
            "worker_id": "w",
            "worker_started_at": "t0",
            "active_count": 1,
            "active_ops": [{"op_id": "h"}],
        }

    def subscribe(_seq: int):
        raise RuntimeError("no subscription")

    async def _noop(*_a, **_k) -> dict[str, Any]:
        return {}

    supervisor = GitWorkerDrainSupervisor(
        store=store,
        begin_drain=_noop,
        drain_state=drain_state,
        subscribe_events=subscribe,
        kill=_noop,
        deadline_s=0.01,
        reconcile_interval_s=0.01,
        progress_interval_s=999.0,
    )
    outcome, _ = await supervisor._await_drain_completed(
        store.get(intent.intent_id),
        time.monotonic() - 10.0,
        time.monotonic(),
        defer_deadline_until_draining=True,
    )
    assert outcome == "cancelled"


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

    assert not await expire_via_cancel(store, intent.intent_id, release_drain=boom)
    stored = store.get(intent.intent_id)
    assert stored is not None
    assert stored.status == "pending_drain"


@pytest.mark.asyncio
async def test_wait_for_boundary_park_live_skips_while_armed(tmp_path) -> None:
    """AC1 a:38111: park_live grace does not fire while GIW reports armed."""
    store = RestartIntentStore(tmp_path / "restart-intents.db")
    intent = store.create_intent(
        service="git_integration_worker",
        action="sync_restart",
        deadline_at="ceiling",
        reason="a38111-ac1",
        wait_for_boundary=True,
        park_live=True,
        caller_agent="cursor",
    )
    park_calls: list[tuple[str, int | None, str]] = []
    worker = _BoundaryParkWorker()

    async def _park(
        intent_id: str, drain_epoch: int | None, reason: str
    ) -> dict[str, Any]:
        park_calls.append((intent_id, drain_epoch, reason))
        return {"requested": [], "refused": [], "already_parked": [], "live_after": 0}

    sup = GitWorkerDrainSupervisor(
        store=store,
        begin_drain=worker.begin_drain,
        drain_state=worker.drain_state,
        subscribe_events=_Feed(),
        kill=_Kill(),
        deadline_s=5.0,
        reconcile_interval_s=0.01,
        progress_interval_s=999.0,
        park_for_restart=_park,
        park_live_grace_s=0.0,
    )
    task = asyncio.create_task(sup.supervise(intent))
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not worker.begun:
            await asyncio.sleep(0.01)
        assert worker.begun, "begin_drain must arm"
        assert worker.begun[0].get("arm") == "holder:sole-1"
        settle = time.monotonic() + 0.2
        while time.monotonic() < settle:
            assert park_calls == []
            await asyncio.sleep(0.01)
        assert worker.phase == "armed"
        assert not task.done() or task.exception() is None
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        elif task.exception() is not None:
            raise task.exception()  # type: ignore[misc]


@pytest.mark.asyncio
async def test_wait_for_boundary_park_live_after_activate(tmp_path) -> None:
    """AC2 a:38111: after drain activate, park_live still sweeps a new occupant."""
    store = RestartIntentStore(tmp_path / "restart-intents.db")
    intent = store.create_intent(
        service="git_integration_worker",
        action="sync_restart",
        deadline_at="ceiling",
        reason="a38111-ac2",
        wait_for_boundary=True,
        park_live=True,
        caller_agent="cursor",
    )
    park_calls: list[tuple[str, int | None, str]] = []
    worker = _BoundaryParkWorker()

    async def _park(
        intent_id: str, drain_epoch: int | None, reason: str
    ) -> dict[str, Any]:
        park_calls.append((intent_id, drain_epoch, reason))
        return {
            "requested": ["new-op"],
            "refused": [],
            "already_parked": [],
            "live_after": 0,
        }

    sup = GitWorkerDrainSupervisor(
        store=store,
        begin_drain=worker.begin_drain,
        drain_state=worker.drain_state,
        subscribe_events=_Feed(),
        kill=_Kill(),
        deadline_s=5.0,
        reconcile_interval_s=0.01,
        progress_interval_s=999.0,
        park_for_restart=_park,
        park_live_grace_s=0.0,
    )
    task = asyncio.create_task(sup.supervise(intent))
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not worker.begun:
            await asyncio.sleep(0.01)
        assert worker.begun and park_calls == []
        worker.activate_with_new_op()
        while time.monotonic() < deadline and not park_calls:
            await asyncio.sleep(0.01)
        assert park_calls, "park_live must run after draining=True"
        assert park_calls[0][0] == intent.intent_id
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        elif task.exception() is not None:
            raise task.exception()  # type: ignore[misc]


@pytest.mark.asyncio
async def test_wait_for_boundary_park_live_skips_on_generation_gone(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GIW restart mid-arm: new generation must not hang or park the old epoch."""
    import scripts.model_manager.ui.controller.git_worker_drain_supervisor as sup_mod

    monkeypatch.setattr(sup_mod, "_GENERATION_GONE_CONFIRM_WINDOW_S", 0.03)
    store = RestartIntentStore(tmp_path / "restart-intents.db")
    intent = store.create_intent(
        service="git_integration_worker",
        action="sync_restart",
        deadline_at="ceiling",
        reason="a38111-gone",
        wait_for_boundary=True,
        park_live=True,
        caller_agent="cursor",
    )
    park_calls: list[tuple[str, int | None, str]] = []
    worker = _BoundaryParkWorker()

    async def _park(
        intent_id: str, drain_epoch: int | None, reason: str
    ) -> dict[str, Any]:
        park_calls.append((intent_id, drain_epoch, reason))
        return {"requested": [], "refused": [], "already_parked": [], "live_after": 0}

    sup = GitWorkerDrainSupervisor(
        store=store,
        begin_drain=worker.begin_drain,
        drain_state=worker.drain_state,
        subscribe_events=_Feed(),
        kill=_Kill(),
        deadline_s=5.0,
        reconcile_interval_s=0.01,
        progress_interval_s=999.0,
        park_for_restart=_park,
        park_live_grace_s=0.0,
    )
    task = asyncio.create_task(sup.supervise(intent))
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not worker.begun:
            await asyncio.sleep(0.01)
        assert worker.begun
        worker.simulate_new_generation()
        while time.monotonic() < deadline and not task.done():
            await asyncio.sleep(0.01)
        assert park_calls == []
        assert task.done(), "generation_gone must resolve, not hang in park wait"
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


class _BoundaryParkWorker:
    """Fake GIW that stays armed until the test flips the drain."""

    def __init__(self) -> None:
        self.begun: list[dict[str, Any]] = []
        self.phase = "pre"
        self.pre = {
            "active_count": 1,
            "draining": False,
            "armed": False,
            "drain_epoch": 0,
            "worker_id": "w",
            "worker_started_at": "t0",
            "active_ops": [{"op_id": "sole-1", "kind": "cursor_sdk"}],
        }
        self.armed = {
            "active_count": 1,
            "draining": False,
            "armed": True,
            "arm": "holder:sole-1",
            "drain_epoch": 1,
            "worker_id": "w",
            "worker_started_at": "t0",
            "active_ops": [{"op_id": "sole-1", "kind": "cursor_sdk"}],
        }
        self.active = {
            "active_count": 1,
            "draining": True,
            "armed": False,
            "drain_epoch": 1,
            "worker_id": "w",
            "worker_started_at": "t0",
            "active_ops": [{"op_id": "new-op", "kind": "cursor_sdk"}],
        }

    async def drain_state(self) -> dict[str, Any]:
        if self.phase == "pre":
            return self.pre
        if self.phase == "armed":
            return self.armed
        if self.phase == "gone":
            return {
                "active_count": 0,
                "draining": False,
                "armed": False,
                "drain_epoch": 0,
                "worker_id": "w-new",
                "worker_started_at": "t1",
                "active_ops": [],
            }
        return self.active

    async def begin_drain(self, body: dict[str, Any]) -> dict[str, Any]:
        self.begun.append(body)
        self.phase = "armed"
        return self.armed

    def activate_with_new_op(self) -> None:
        self.phase = "active"

    def simulate_new_generation(self) -> None:
        self.phase = "gone"


@pytest.mark.asyncio
async def test_wait_for_boundary_park_live_skips_when_giw_dead(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R1: armed GIW whose drain_state is gone and pid is absent must not hang."""
    import scripts.model_manager.ui.controller.git_worker_liveness as live_mod

    monkeypatch.setattr(live_mod, "PROBE_UNREACHABLE_WINDOW_S", 0.03)
    store = RestartIntentStore(tmp_path / "restart-intents.db")
    intent = store.create_intent(
        service="git_integration_worker",
        action="sync_restart",
        deadline_at="ceiling",
        reason="a38111-r1",
        wait_for_boundary=True,
        park_live=True,
        caller_agent="cursor",
    )
    park_calls: list[tuple[str, int | None, str]] = []
    worker = _BoundaryParkWorker()

    async def _park(
        intent_id: str, drain_epoch: int | None, reason: str
    ) -> dict[str, Any]:
        park_calls.append((intent_id, drain_epoch, reason))
        return {"requested": [], "refused": [], "already_parked": [], "live_after": 0}

    orig_drain = worker.drain_state
    orig_begin = worker.begin_drain
    post_arm = {"gone": False}

    async def _begin(body: dict[str, Any]) -> dict[str, Any]:
        snap = await orig_begin(body)
        post_arm["gone"] = True
        return snap

    async def _drain() -> dict[str, Any]:
        if post_arm["gone"]:
            return None  # type: ignore[return-value]
        return await orig_drain()

    async def _absent() -> bool:
        return True

    sup = GitWorkerDrainSupervisor(
        store=store,
        begin_drain=_begin,
        drain_state=_drain,
        subscribe_events=_Feed(),
        kill=_Kill(),
        deadline_s=5.0,
        reconcile_interval_s=0.01,
        progress_interval_s=999.0,
        park_for_restart=_park,
        park_live_grace_s=0.0,
        process_absent=_absent,
    )
    task = asyncio.create_task(sup.supervise(intent))
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not worker.begun:
            await asyncio.sleep(0.01)
        assert worker.begun
        while time.monotonic() < deadline and not task.done():
            await asyncio.sleep(0.01)
        assert park_calls == []
        assert task.done(), "dead GIW must leave the park wait"
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


@pytest.mark.asyncio
async def test_wait_for_boundary_park_live_expires_ttl_while_armed(
    tmp_path,
) -> None:
    """R2: caller TTL must cancel during the pre-draining park wait."""
    store = RestartIntentStore(tmp_path / "restart-intents.db")
    intent = store.create_intent(
        service="git_integration_worker",
        action="sync_restart",
        deadline_at="ceiling",
        reason="a38111-r2",
        wait_for_boundary=True,
        park_live=True,
        intent_ttl_s=0.05,
        caller_agent="cursor",
    )
    past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    store._update(intent.intent_id, expires_at=past)
    park_calls: list[tuple[str, int | None, str]] = []
    worker = _BoundaryParkWorker()
    released: list[tuple[str, int]] = []

    async def _park(
        intent_id: str, drain_epoch: int | None, reason: str
    ) -> dict[str, Any]:
        park_calls.append((intent_id, drain_epoch, reason))
        return {"requested": [], "refused": [], "already_parked": [], "live_after": 0}

    async def _release(iid: str, epoch: int) -> dict[str, Any]:
        released.append((iid, epoch))
        return {"draining": False, "armed": False}

    sup = GitWorkerDrainSupervisor(
        store=store,
        begin_drain=worker.begin_drain,
        drain_state=worker.drain_state,
        subscribe_events=_Feed(),
        kill=_Kill(),
        deadline_s=5.0,
        reconcile_interval_s=0.01,
        progress_interval_s=0.01,
        park_for_restart=_park,
        park_live_grace_s=0.0,
        cancel_drain=_release,
    )
    task = asyncio.create_task(sup.supervise(intent))
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not worker.begun:
            await asyncio.sleep(0.01)
        assert worker.begun
        while time.monotonic() < deadline and not task.done():
            await asyncio.sleep(0.01)
        assert park_calls == []
        assert released, "TTL expiry must release the armed drain"
        stored = store.get(intent.intent_id)
        assert stored is not None
        assert stored.status == "cancelled"
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
