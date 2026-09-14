"""Regression: the cursor-auto lane loop must outlive a failing iteration.

2026-08-09: ``auto_worker_loop`` unwound at 21:27:58Z when ``await hb_task``
re-raised the exception of a heartbeat writer that had already died mid-wedge.
The loop's ``finally`` deregistered the handler, ``lane:cursor-auto`` read
``handler_count: 0`` for the next four hours, and every ``agent_bus.request``
parked behind it — including ``contract: propagate``, the repair path. Nothing
logged, because the task is pinned on ``app.state`` and so never garbage
collected into asyncio's "exception was never retrieved" warning.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from services.git_integration_worker.cursor_auto import auto_worker_loop as mod
from services.git_integration_worker.cursor_auto.liveness import AutoLivenessRegistry


async def _run_loop_briefly(
    app: SimpleNamespace,
    registry: AutoLivenessRegistry,
    *,
    ticks: float = 0.25,
) -> bool:
    """Run the lane loop, sample liveness while it runs, then cancel it.

    Liveness must be read *before* cancelling: the loop's ``finally`` legitimately
    deregisters the handler on shutdown, which would mask the thing under test.
    """
    task = asyncio.create_task(mod.auto_worker_loop(app))
    await asyncio.sleep(ticks)
    still_running = not task.done()
    live_while_running = registry.is_live()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    if not still_running:
        # Surface the real cause rather than a bare assert.
        raise AssertionError(f"loop exited early: {task.exception()!r}")
    return live_while_running


@pytest.mark.asyncio
async def test_loop_survives_claim_next_raising() -> None:
    """A raising queue must not end the lane; the handler stays registered."""
    registry = AutoLivenessRegistry()
    app = SimpleNamespace(state=SimpleNamespace(admission_controller=None))
    calls = {"n": 0}

    def _boom() -> None:
        calls["n"] += 1
        raise RuntimeError("database is locked")

    queue = SimpleNamespace(claim_next=_boom)
    with (
        patch.object(mod, "get_registry", return_value=registry),
        patch.object(mod, "get_queue", return_value=queue),
        patch.object(mod, "_WORKER_INTERVAL_S", 0.01),
    ):
        live = await _run_loop_briefly(app, registry)

    assert calls["n"] > 1, "loop stopped claiming after the first failure"
    assert live, "handler was deregistered by a failing iteration"


@pytest.mark.asyncio
async def test_loop_survives_dead_heartbeat_writer() -> None:
    """The 21:27:58Z shape: a dead heartbeat must not unwind the loop.

    ``bump_heartbeat`` raises, so the heartbeat task dies holding a
    non-``CancelledError``; the loop's ``finally`` then awaits it. Before the
    fix only ``CancelledError`` was caught, so that await re-raised and killed
    the lane.
    """
    registry = AutoLivenessRegistry()
    app = SimpleNamespace(
        state=SimpleNamespace(
            admission_controller=None, worker_id="w", worker_boot_ts="t"
        )
    )
    job = SimpleNamespace(job_id="job-1")
    handed_out = {"n": 0}

    def _claim_once():
        handed_out["n"] += 1
        return job if handed_out["n"] == 1 else None

    def _bump(_job_id: str) -> None:
        raise RuntimeError("database is locked")

    queue = SimpleNamespace(
        claim_next=_claim_once,
        bump_heartbeat=_bump,
        mark_done=lambda *_a, **_k: None,
    )

    async def _process(*_a, **_k):
        await asyncio.sleep(0.05)
        return {"ok": True, "terminal_status": "completed"}

    with (
        patch.object(mod, "get_registry", return_value=registry),
        patch.object(mod, "get_queue", return_value=queue),
        patch.object(mod, "process_job", _process),
        patch.object(mod, "_WORKER_INTERVAL_S", 0.01),
    ):
        live = await _run_loop_briefly(app, registry)

    assert live, "dead heartbeat writer deregistered the handler"


def test_failure_sleep_s_doubles_to_ceiling() -> None:
    """Fix 3: consecutive failures back off exponentially up to 30s."""
    assert mod._failure_sleep_s(0, 0.5) == 0.5
    assert mod._failure_sleep_s(1, 0.5) == 0.5
    assert mod._failure_sleep_s(2, 0.5) == 1.0
    assert mod._failure_sleep_s(3, 0.5) == 2.0
    assert mod._failure_sleep_s(7, 0.5) == 30.0
    assert mod._failure_sleep_s(99, 0.5) == 30.0


@pytest.mark.asyncio
async def test_loop_backoff_grows_on_failures_and_resets_on_success() -> None:
    """Fix 3: failing iterations sleep longer; a success restores base interval."""
    registry = AutoLivenessRegistry()
    app = SimpleNamespace(state=SimpleNamespace(admission_controller=None))
    calls = {"n": 0}
    backoff_calls: list[tuple[int, float]] = []

    def _boom() -> None:
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError("transient fault")
        return None

    done = asyncio.Event()
    real_failure_sleep = mod._failure_sleep_s

    def _record_backoff(fail_streak: int, base_interval_s: float) -> float:
        backoff_calls.append((fail_streak, base_interval_s))
        if len(backoff_calls) >= 3:
            done.set()
        return real_failure_sleep(fail_streak, base_interval_s)

    queue = SimpleNamespace(claim_next=_boom)

    with (
        patch.object(mod, "get_registry", return_value=registry),
        patch.object(mod, "get_queue", return_value=queue),
        patch.object(mod, "_WORKER_INTERVAL_S", 0.5),
        patch.object(mod, "_failure_sleep_s", side_effect=_record_backoff),
    ):
        task = asyncio.create_task(mod.auto_worker_loop(app))
        await asyncio.wait_for(done.wait(), timeout=2.0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert len(backoff_calls) >= 3
    assert backoff_calls[0] == (1, 0.5)
    assert backoff_calls[1] == (2, 0.5)
    assert backoff_calls[2] == (0, 0.5)
