"""Pre-arm lease-wedge bind: arming reap, drain reap-only, bounded slot acquire.

Regression cover for dispatch ``38611b297c16-4a1462e7``, which took the exclusive
write lease at 21:35:50Z and held it ~83 minutes with a null heartbeat while two
deferred drains timed out on it. See
``cortex://notes/system/threads/5819-giw-pre-arm-bind.md``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    _connect,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

REPO = "/repo"
WORKER = "worker-a"
ARMING_S = 300.0
THRESHOLD_S = 1980.0
DEAD_GRACE_S = 60.0


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


def _req(dispatch_id: str) -> CursorDispatchRequest:
    return CursorDispatchRequest(
        thread_id=f"t-{dispatch_id}",
        model="cursor/composer-2.5",
        dispatch_id=dispatch_id,
        execution_id=f"exec-{dispatch_id}",
        message="hello",
    )


def _admit(ledger: CursorDispatchLedger, dispatch_id: str) -> None:
    req = _req(dispatch_id)
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            model_id="composer-2.5",
        ),
        source_repo=REPO,
        contract="implement",
        worker_instance=WORKER,
    )


def _age_row(dispatch_id: str, *, started_s_ago: float, heartbeat_s_ago: float | None):
    """Backdate a running row's clocks; heartbeat None reproduces never-armed."""
    now = datetime.now(UTC)
    started = (now - timedelta(seconds=started_s_ago)).isoformat()
    heartbeat = (
        None
        if heartbeat_s_ago is None
        else (now - timedelta(seconds=heartbeat_s_ago)).isoformat()
    )
    with _connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches "
            "SET status='running', started_at=?, last_heartbeat_at=? "
            "WHERE dispatch_id=?",
            (started, heartbeat, dispatch_id),
        )


def _live_task() -> MagicMock:
    task = MagicMock(spec=asyncio.Task)
    task.done.return_value = False
    return task


def _stale(ledger: CursorDispatchLedger, *, arming_timeout_s: float | None = ARMING_S):
    return ledger.stale_writers(
        threshold_s=THRESHOLD_S,
        dead_run_grace_s=DEAD_GRACE_S,
        worker_instance=WORKER,
        arming_timeout_s=arming_timeout_s,
    )


# --------------------------------------------------------------------------
# C' — arming horizon keyed on last_heartbeat_at
# --------------------------------------------------------------------------


def test_never_armed_holder_reaps_on_arming_horizon() -> None:
    """Null heartbeat past the arming horizon is stale despite a live task.

    Without the arming cap this row sits inside threshold_s (1980s) and is
    invisible to the sweeper for 33 minutes.
    """
    ledger = CursorDispatchLedger.instance()
    _admit(ledger, "wedged")
    _age_row("wedged", started_s_ago=400.0, heartbeat_s_ago=None)
    ledger.register_task("wedged", _live_task())

    assert _stale(ledger) == ["wedged"]
    assert _stale(ledger, arming_timeout_s=None) == []


def test_never_armed_holder_inside_arming_horizon_survives() -> None:
    """A dispatch still completing its bridge handshake must not be reaped."""
    ledger = CursorDispatchLedger.instance()
    _admit(ledger, "launching")
    _age_row("launching", started_s_ago=60.0, heartbeat_s_ago=None)
    ledger.register_task("launching", _live_task())

    assert _stale(ledger) == []


def test_armed_holder_survives_arming_horizon() -> None:
    """A heartbeating holder is armed; the arming cap must not apply to it."""
    ledger = CursorDispatchLedger.instance()
    _admit(ledger, "healthy")
    _age_row("healthy", started_s_ago=4000.0, heartbeat_s_ago=10.0)
    ledger.register_task("healthy", _live_task())

    assert _stale(ledger) == []


def test_null_sdk_agent_id_alone_never_implies_stale() -> None:
    """Guard the rejected D3 predicate.

    ``record_sdk_identity`` stores NULL for every dispatch because the local
    bridge agent exposes no ``id``. Keying arming on ``sdk_agent_id`` would reap
    healthy live work, so a heartbeating row with a null agent id must survive.
    """
    ledger = CursorDispatchLedger.instance()
    _admit(ledger, "no-agent-id")
    _age_row("no-agent-id", started_s_ago=4000.0, heartbeat_s_ago=5.0)
    ledger.register_task("no-agent-id", _live_task())

    with _connect() as conn:
        row = conn.execute(
            "SELECT sdk_agent_id FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            ("no-agent-id",),
        ).fetchone()
    assert row["sdk_agent_id"] is None
    assert _stale(ledger) == []


# --------------------------------------------------------------------------
# N2 — forced release over the live-task veto
# --------------------------------------------------------------------------


def test_release_stale_writer_live_task_veto_and_force_override() -> None:
    """A wedged holder keeps its task live forever; force must still release."""
    ledger = CursorDispatchLedger.instance()
    _admit(ledger, "wedged")
    _age_row("wedged", started_s_ago=400.0, heartbeat_s_ago=None)
    ledger.register_task("wedged", _live_task())

    assert ledger.release_stale_writer(dispatch_id="wedged") is None
    assert ledger.release_stale_writer(dispatch_id="wedged", force=True) == REPO

    with _connect() as conn:
        row = conn.execute(
            "SELECT status, terminal_status FROM cursor_sdk_dispatches "
            "WHERE dispatch_id=?",
            ("wedged",),
        ).fetchone()
    assert row["status"] == "failed"
    assert row["terminal_status"] == "failed"


# --------------------------------------------------------------------------
# B — sweeper reaps while draining, without promoting
# --------------------------------------------------------------------------


def _controller() -> MagicMock:
    controller = MagicMock()
    controller.worker_id = WORKER
    return controller


@pytest.mark.asyncio
async def test_reconcile_reap_only_releases_without_promoting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drain path: clear the wedged holder, do not start its successor."""
    from services.git_integration_worker.routes import cursor_sdk as route

    ledger = CursorDispatchLedger.instance()
    _admit(ledger, "wedged")
    _admit(ledger, "successor")
    _age_row("wedged", started_s_ago=400.0, heartbeat_s_ago=None)
    ledger.register_task("wedged", _live_task())

    promoted: list[str] = []

    async def _fake_promote(*, lease_key: str, controller, request=None) -> None:
        promoted.append(lease_key)

    monkeypatch.setattr(route, "_promote_queued_for_lease", _fake_promote)
    monkeypatch.setattr(route, "_SDK_ARM_TIMEOUT_S", ARMING_S)

    await route.reconcile_stale_leases(_controller(), reap_only=True)

    assert promoted == []
    with _connect() as conn:
        row = conn.execute(
            "SELECT status FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            ("wedged",),
        ).fetchone()
    assert row["status"] == "failed", "reap must happen even in reap_only mode"


@pytest.mark.asyncio
async def test_reconcile_promotes_when_not_draining(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Steady state keeps the promote half of the sweep."""
    from services.git_integration_worker.routes import cursor_sdk as route

    ledger = CursorDispatchLedger.instance()
    _admit(ledger, "wedged")
    _admit(ledger, "successor")
    _age_row("wedged", started_s_ago=400.0, heartbeat_s_ago=None)
    ledger.register_task("wedged", _live_task())

    promoted: list[str] = []

    async def _fake_promote(*, lease_key: str, controller, request=None) -> None:
        promoted.append(lease_key)

    monkeypatch.setattr(route, "_promote_queued_for_lease", _fake_promote)
    monkeypatch.setattr(route, "_SDK_ARM_TIMEOUT_S", ARMING_S)

    await route.reconcile_stale_leases(_controller())

    assert promoted == [REPO]


# --------------------------------------------------------------------------
# E — bounded capacity-slot acquire
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slot_acquire_timeout_raises_and_leaves_no_holder() -> None:
    """An unavailable slot must fail loudly rather than wedge upstream of the
    outer watchdog, and must not leave the timed-out id registered as holder."""
    from services.git_integration_worker.cursor_sdk_gate import (
        _GATE,
        acquire_sdk_dispatch_slot,
        release_sdk_dispatch_slot,
    )

    await acquire_sdk_dispatch_slot(dispatch_id="holder")
    try:
        with pytest.raises(TimeoutError):
            await acquire_sdk_dispatch_slot(dispatch_id="latecomer", timeout=0.05)
        assert "latecomer" not in _GATE.holders
        assert _GATE.queue_length == 0
    finally:
        await release_sdk_dispatch_slot(dispatch_id="holder")

    assert _GATE.active_count == 0


@pytest.mark.asyncio
async def test_slot_acquire_is_idempotent_for_existing_holder() -> None:
    """Nest park transfers capacity to the child before the gated run, so the
    child's bounded acquire must return immediately rather than time out."""
    from services.git_integration_worker.cursor_sdk_gate import (
        _GATE,
        acquire_sdk_dispatch_slot,
        release_sdk_dispatch_slot,
    )

    await acquire_sdk_dispatch_slot(dispatch_id="parent")
    try:
        await acquire_sdk_dispatch_slot(dispatch_id="parent", timeout=0.05)
        assert _GATE.active_count == 1
    finally:
        await release_sdk_dispatch_slot(dispatch_id="parent")
