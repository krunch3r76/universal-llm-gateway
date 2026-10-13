"""Watchdog sweep must not block the agent-bus asyncio loop (friction:39179)."""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from agent_bus_store import watchdog
from agent_bus_store.db import admit_dispatch, create_thread_with_turn, init_db
from agent_bus_store.db.connection import connect
from agent_bus_store.db.turns import insert_turn
from agent_bus_store.sdk_liveness import ProbeResult
from agent_bus_store.watchdog import _sweep, run_watchdog

pytestmark = pytest.mark.offline


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    return db_path


def _admit_stale_sdk_thread(*, slug: str, execution_id: str) -> str:
    thread_row, *_ = create_thread_with_turn(
        slug=slug,
        from_agent="dispatch",
        to_agent="claude-cursor",
        subject="cursor-sdk generate",
        body="pointer only",
        lifecycle_state="pending",
    )
    thread_id = thread_row["id"]
    admit_dispatch(
        thread_id=thread_id,
        execution_id=execution_id,
        pipeline_id="cursor-sdk-generate",
        caller_agent="cursor-web",
    )
    old = (datetime.now(UTC) - timedelta(seconds=4000)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with connect() as conn:
        conn.execute(
            "UPDATE threads SET updated_at = ? WHERE id = ?",
            (old, thread_id),
        )
    return thread_id


def _seed_reconcile_link(*, slug: str, execution_id: str) -> str:
    thread_row, *_ = create_thread_with_turn(
        slug=slug,
        from_agent="dispatch",
        to_agent="claude-cursor",
        subject="cursor-sdk generate",
        body="work",
        lifecycle_state="pending",
    )
    thread_id = thread_row["id"]
    admit_dispatch(
        thread_id=thread_id,
        execution_id=execution_id,
        pipeline_id="cursor-sdk-generate",
        caller_agent="cursor-web",
    )
    with connect() as conn:
        conn.execute(
            "UPDATE threads SET bus_lifecycle_state = 'active' WHERE id = ?",
            (thread_id,),
        )
    return thread_id


def _seed_active_stale(*, slug: str, execution_id: str, age_s: float = 200_000) -> str:
    thread_id = _seed_reconcile_link(slug=slug, execution_id=execution_id)
    old = (datetime.now(UTC) - timedelta(seconds=age_s)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with connect() as conn:
        conn.execute(
            "UPDATE threads SET updated_at = ?, created_at = ? WHERE id = ?",
            (old, old, thread_id),
        )
    return thread_id


@pytest.mark.asyncio
async def test_reapers_run_in_worker_thread(bus_db) -> None:
    """Real _reap_pending, _reap_admitted, and _reap_active run in a worker thread.

    The caller supplies ``asyncio.to_thread(_sweep)``. This does not discriminate
    an inline ``run_watchdog``: the same reapers would pass on the pre-fix
    sweep if this test wrapped them itself.
    """
    for i in range(5):
        _admit_stale_sdk_thread(slug=f"off-loop-{i}", execution_id=f"exec-off-{i}")

    loop = asyncio.get_running_loop()
    probe_entered = asyncio.Event()
    real_reap_admitted = watchdog._reap_admitted

    def slow_probe(_thread_id: str) -> ProbeResult:
        try:
            asyncio.get_running_loop()
            probe_entered.set()
        except RuntimeError:
            loop.call_soon_threadsafe(probe_entered.set)
        time.sleep(2.0)
        return ProbeResult(
            payload={"status": "running", "execution_id": "exec-live"},
            http_status=200,
            error=None,
        )

    def _reap_admitted_slow(cutoff: str) -> None:
        real_reap_admitted(cutoff, probe_fn=slow_probe)

    async def stand_in_create_turn() -> float:
        t0 = time.monotonic()
        await asyncio.sleep(0)
        return time.monotonic() - t0

    with (
        patch(
            "agent_bus_store.watchdog._reap_admitted",
            side_effect=_reap_admitted_slow,
        ),
        patch(
            "agent_bus_store.reconcile.reconcile_orphaned_dispatches",
            return_value=0,
        ),
        patch("agent_bus_store.quiet_sweep.sweep_quiet_with_wip", return_value=0),
    ):
        sweep_task = asyncio.create_task(asyncio.to_thread(_sweep))
        await asyncio.wait_for(probe_entered.wait(), timeout=10.0)
        elapsed = await asyncio.wait_for(stand_in_create_turn(), timeout=0.5)
        await asyncio.wait_for(sweep_task, timeout=15.0)

    assert elapsed < 0.5, (
        f"event loop blocked {elapsed:.2f}s while _sweep held sync probes"
    )


@pytest.mark.asyncio
async def test_run_watchdog_slow_probe_does_not_stall_loop(
    bus_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_watchdog keeps the asyncio loop responsive while a worker probe sleeps.

    Loop-liveness proxy (not an HTTP POST): latency is sampled from a worker
    thread after a threading.Event handshake via call_soon_threadsafe. An inline
    sweep blocks that callback for the whole probe sleep. POST /turns handlers
    use asyncio.to_thread end-to-end, so loop liveness implies handler progress.
    """
    monkeypatch.setattr(watchdog, "_SWEEP_INTERVAL", 0)
    _admit_stale_sdk_thread(slug="slow-admitted", execution_id="exec-adm")
    gate = threading.Event()
    probe_thread: list[int] = []
    probe_sleep_s = 1.0
    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()

    def slow_probe(_thread_id: str) -> ProbeResult:
        probe_thread.append(threading.get_ident())
        gate.set()
        time.sleep(probe_sleep_s)
        return ProbeResult(
            payload={"status": "running", "execution_id": "exec-live"},
            http_status=200,
            error=None,
        )

    real_reap_admitted = watchdog._reap_admitted

    def _reap_admitted_slow(cutoff: str) -> None:
        real_reap_admitted(cutoff, probe_fn=slow_probe)

    def measure_loop_latency() -> tuple[bool, float]:
        assert gate.wait(10), "slow probe never entered the sweep"
        assert gate.is_set()
        acknowledged = threading.Event()
        t0 = time.monotonic()
        loop.call_soon_threadsafe(acknowledged.set)
        woke = acknowledged.wait(0.5)
        return woke, time.monotonic() - t0

    with patch(
        "agent_bus_store.watchdog._reap_admitted",
        side_effect=_reap_admitted_slow,
    ):
        wd = asyncio.create_task(run_watchdog())
        try:
            woke, elapsed = await asyncio.to_thread(measure_loop_latency)
        finally:
            wd.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(wd, timeout=15.0)

    assert woke, (
        f"loop did not run a callback within 0.5s during the probe "
        f"(elapsed={elapsed:.3f}s)"
    )
    assert elapsed < 0.5, f"loop latency {elapsed:.2f}s during run_watchdog sweep"
    assert probe_thread and probe_thread[0] != loop_thread, (
        "admitted probe ran on the event-loop thread"
    )


@pytest.mark.asyncio
async def test_watchdog_cancel_waits_for_in_flight_sweep(
    bus_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancel during _sweep returns only after the worker thread finishes (bounded)."""
    monkeypatch.setattr(watchdog, "_SWEEP_INTERVAL", 0)
    sweep_hold_s = 1.2
    sweep_started = threading.Event()

    def slow_sweep() -> None:
        sweep_started.set()
        time.sleep(sweep_hold_s)

    max_join_s = 5.0
    monkeypatch.setattr(watchdog, "_SWEEP_CANCEL_JOIN_S", max_join_s)

    with patch("agent_bus_store.watchdog._sweep", side_effect=slow_sweep):
        wd = asyncio.create_task(run_watchdog())
        assert await asyncio.to_thread(sweep_started.wait, 3)
        t0 = time.monotonic()
        wd.cancel()
        with pytest.raises(asyncio.CancelledError):
            await wd
        elapsed = time.monotonic() - t0

    assert elapsed >= sweep_hold_s * 0.85, (
        f"cancel returned before sweep thread finished ({elapsed:.2f}s)"
    )
    assert elapsed <= max_join_s, (
        f"cancel join exceeded bound ({elapsed:.2f}s > {max_join_s}s)"
    )


def test_turn_posted_between_select_and_update_is_not_abandoned(bus_db) -> None:
    """A turn committed after the stale select must not be abandoned."""
    thread_id = _seed_active_stale(slug="race-active", execution_id="exec-race")
    real = watchdog._reap_single
    calls = {"n": 0}

    def hooked(thread_id: str, **kwargs: object) -> bool:
        calls["n"] += 1
        insert_turn(
            thread=thread_id,
            from_agent="dispatch",
            to_agent="cursor-auto",
            subject="still working",
            body="turn landed between select and update",
        )
        return real(thread_id, **kwargs)  # type: ignore[arg-type]

    with patch("agent_bus_store.watchdog._reap_single", side_effect=hooked):
        watchdog._reap_active(watchdog._cutoff_for_ttl(watchdog._ACTIVE_ABANDON_TTL))

    assert calls["n"] == 1
    with connect() as conn:
        row = conn.execute(
            "SELECT bus_lifecycle_state, status FROM threads WHERE id = ?",
            (thread_id,),
        ).fetchone()
        turns = conn.execute(
            "SELECT COUNT(*) AS n FROM turns WHERE thread = ?",
            (thread_id,),
        ).fetchone()
    assert row is not None
    assert row["bus_lifecycle_state"] == "active"
    assert row["status"] != "closed"
    assert int(turns["n"]) >= 2


@pytest.mark.asyncio
async def test_cancel_before_sweep_start_skips_join(
    bus_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Saturated executor: cancel while _run is queued returns without the join bound."""
    monkeypatch.setattr(watchdog, "_SWEEP_INTERVAL", 0)
    monkeypatch.setattr(watchdog, "_SWEEP_CANCEL_JOIN_S", 5.0)
    loop = asyncio.get_running_loop()
    pool = concurrent.futures.ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="wd-sat"
    )
    previous = getattr(loop, "_default_executor", None)
    loop.set_default_executor(pool)
    release = threading.Event()
    blocker = pool.submit(release.wait)
    queued = threading.Event()
    original = asyncio.to_thread

    async def _wrapped(func, /, *args, **kwargs):
        if getattr(func, "__name__", "") == "_run":
            queued.set()
        return await original(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _wrapped)
    wd = asyncio.create_task(run_watchdog())
    elapsed = 999.0
    try:
        for _ in range(200):
            if queued.is_set():
                break
            await asyncio.sleep(0.01)
        assert queued.is_set(), "sweep was never queued on the saturated executor"
        t0 = time.monotonic()
        wd.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(wd, timeout=1.5)
        elapsed = time.monotonic() - t0
    finally:
        release.set()
        blocker.result(timeout=5)
        if previous is None:
            loop.set_default_executor(
                concurrent.futures.ThreadPoolExecutor(thread_name_prefix="asyncio")
            )
        else:
            loop.set_default_executor(previous)
        pool.shutdown(wait=False)

    assert elapsed < 1.0, f"queued cancel waited {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_cancel_mid_sweep_joins_at_hold_not_bound(
    bus_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancel while _reap_pending sleeps joins at the hold, then the raise lands in finally."""
    monkeypatch.setattr(watchdog, "_SWEEP_INTERVAL", 0)
    hold_s = 1.2
    join_bound_s = 5.0
    monkeypatch.setattr(watchdog, "_SWEEP_CANCEL_JOIN_S", join_bound_s)
    entered = threading.Event()

    def slow_then_raise(_cutoff: str) -> None:
        entered.set()
        time.sleep(hold_s)
        raise RuntimeError("reap failed mid-sweep")

    with patch("agent_bus_store.watchdog._reap_pending", side_effect=slow_then_raise):
        wd = asyncio.create_task(run_watchdog())
        assert await asyncio.to_thread(entered.wait, 3)
        t0 = time.monotonic()
        wd.cancel()
        with pytest.raises(asyncio.CancelledError):
            await wd
        elapsed = time.monotonic() - t0

    assert elapsed >= hold_s * 0.85, (
        f"join returned before the in-flight reap finished ({elapsed:.2f}s)"
    )
    assert elapsed < hold_s + 0.8, (
        f"join tracked the bound, not the hold ({elapsed:.2f}s vs {hold_s}s)"
    )
    assert elapsed < join_bound_s
