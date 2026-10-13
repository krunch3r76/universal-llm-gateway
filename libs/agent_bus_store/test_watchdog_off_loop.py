"""Watchdog sweep must not block the agent-bus asyncio loop (friction:39179)."""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from agent_bus_store import watchdog
from agent_bus_store.db import admit_dispatch, create_thread_with_turn, init_db
from agent_bus_store.db.connection import connect, write_connect
from agent_bus_store.sdk_liveness import LivenessVerdict, ProbeResult
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


def _seed_quiet_wip_thread(*, slug: str, execution_id: str) -> str:
    seat = "web-anthropic"
    thread_row, *_ = create_thread_with_turn(
        slug=slug,
        from_agent=seat,
        to_agent="cursor-auto",
        subject="commission work",
        body="please run",
        lifecycle_state="pending",
    )
    thread_id = thread_row["id"]
    admit_dispatch(
        thread_id=thread_id,
        execution_id=execution_id,
        pipeline_id="cursor-sdk-generate",
        caller_agent=seat,
    )
    old = (datetime.now(UTC) - timedelta(seconds=120)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with connect() as conn:
        conn.execute(
            "UPDATE turns SET created_at = ? WHERE thread = ?",
            (old, thread_id),
        )
        conn.execute(
            "UPDATE threads SET bus_lifecycle_state = 'active', updated_at = ? "
            "WHERE id = ?",
            (old, thread_id),
        )
    return thread_id


def _slow_probe_factory(*, delay_s: float):
    def _slow_probe(_thread_id: str) -> ProbeResult:
        time.sleep(delay_s)
        return ProbeResult(
            payload={"status": "running", "execution_id": "exec-live"},
            http_status=200,
            error=None,
        )

    return _slow_probe


@pytest.mark.asyncio
async def test_sweep_runs_off_loop(bus_db, monkeypatch: pytest.MonkeyPatch) -> None:
    """Five 2s admitted probes in _sweep must not stall a concurrent turn coroutine."""
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
async def test_slow_probes_do_not_stall_turn_post(
    bus_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full _sweep with slow admitted, reconcile, and quiet probes stays off-loop."""
    monkeypatch.setattr(watchdog, "_SWEEP_INTERVAL", 0)
    _admit_stale_sdk_thread(slug="slow-admitted", execution_id="exec-adm")
    _seed_reconcile_link(slug="slow-reconcile", execution_id="exec-rec")
    _seed_quiet_wip_thread(slug="slow-quiet", execution_id="exec-quiet")

    delay_s = 0.35

    def slow_evaluate(*args, **kwargs):
        time.sleep(delay_s)
        return (LivenessVerdict.SKIP_LIVE, "running", None)

    def slow_witness(**kwargs):
        time.sleep(delay_s)
        return "live"

    write_hold_started = threading.Event()

    def hold_write_ticket_during_sweep() -> None:
        with write_connect():
            write_hold_started.set()
            time.sleep(1.0)

    async def loop_latency_probe() -> float:
        t0 = time.monotonic()
        await asyncio.sleep(0)
        return time.monotonic() - t0

    slow_admitted = _slow_probe_factory(delay_s=delay_s)
    real_reap_admitted = watchdog._reap_admitted

    def _reap_admitted_slow(cutoff: str) -> None:
        real_reap_admitted(cutoff, probe_fn=slow_admitted)

    with (
        patch(
            "agent_bus_store.watchdog._reap_admitted",
            side_effect=_reap_admitted_slow,
        ),
        patch(
            "agent_bus_store.reconcile.evaluate_link_liveness",
            side_effect=slow_evaluate,
        ),
        patch(
            "agent_bus_store.quiet_sweep.reader_liveness_witness",
            side_effect=slow_witness,
        ),
        patch(
            "agent_bus_store.quiet_sweep._licensed_park",
            return_value=False,
        ),
        patch(
            "agent_bus_store.quiet_sweep.emit_quiet_with_wip_fired",
            return_value=None,
        ),
    ):
        threading.Thread(target=hold_write_ticket_during_sweep, daemon=True).start()
        assert await asyncio.to_thread(write_hold_started.wait, 3.0)
        wd = asyncio.create_task(run_watchdog())
        try:
            elapsed = await asyncio.wait_for(loop_latency_probe(), timeout=0.5)
        finally:
            wd.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(wd, timeout=10.0)

    assert elapsed < 0.5, f"loop latency {elapsed:.2f}s during slow full _sweep"


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
        try:
            time.sleep(sweep_hold_s)
        finally:
            done = watchdog._sweep_inflight_done
            if done is not None:
                done.set()

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
