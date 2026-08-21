"""Background worker loops for the cursor-auto admit lane."""

from __future__ import annotations

import asyncio
import os
import signal
from typing import Any

from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.handler import process_job
from services.git_integration_worker.cursor_auto.liveness import get_registry
from services.git_integration_worker.cursor_auto.queue import get_queue
from services.git_integration_worker.cursor_auto.queue_health_events import (
    emit_concurrent_claimed,
)
from services.git_integration_worker.cursor_auto.terminal_reason_codec import (
    format_exception_reason,
)

logger = get_logger(__name__)

_HANDLER_ID = "cursor-auto-primary"
_WORKER_INTERVAL_S = 0.5
_CONCURRENT_POLL_INTERVAL_S = 0.5
_ORPHAN_INTERVAL_S = 15.0


def drain_blocks_new_auto_claims(controller: Any | None) -> bool:
    """True when GIW drain is live, so the worker must not claim another job.

    Claimed Auto occupancy holds drain idle. Claiming during drain restocks
    that occupancy and can postpone SIGTERM until the queue empties.
    """
    return controller is not None and bool(controller.is_draining())


def drain_belt_fires(controller: Any | None) -> bool:
    """R2′ belt: ``_draining ∧ amber ∧ stalled`` (never bare amber)."""
    if not drain_blocks_new_auto_claims(controller):
        return False
    waiter = get_queue().waiter_starvation()
    if not waiter.get("amber"):
        return False
    snap = controller.drain_state()
    return bool(snap.get("stalled"))


def request_giw_belt_exit(*, reason: str) -> None:
    """Fail-closed GIW self-exit — join-invariant second disjunct.

    SIGTERM this process so the drain latch dies with the generation. Tests
    monkeypatch this callable; production must not ``release_drain``.
    """
    from services.git_integration_worker import git_worker_drain_events as drain_events

    drain_events.emit_drain_belt_exit(reason=reason)
    logger.critical("giw drain belt exit: %s", reason)
    os.kill(os.getpid(), signal.SIGTERM)


async def auto_worker_loop(app: Any) -> None:
    """Background: heartbeat + claim/process queued Auto jobs.

    Heartbeat must continue during long ``process_job`` awaits (nested SDK
    often exceeds ``heartbeat_ttl_s``). Otherwise enqueue sees a dead handler
    mid-job and the next ``agent_bus.request`` 503s — 5867 DIRECTIVE-4 class.
    """
    registry = get_registry()
    registry.register(_HANDLER_ID)
    logger.info("cursor-auto worker loop started handler_id=%s", _HANDLER_ID)

    async def _heartbeat_while_busy(job_id: str) -> None:
        while True:
            registry.heartbeat(_HANDLER_ID)
            try:
                get_queue().bump_heartbeat(job_id)
            except Exception:
                # A failed ledger write must not end this task. The registry
                # refresh above is what keeps the lane armed; if this coroutine
                # dies the handler is pruned 30s later and every subsequent
                # agent_bus.request 503s with no turn and no alarm.
                logger.exception(
                    "cursor-auto heartbeat ledger write failed job=%s", job_id
                )
            await asyncio.sleep(min(5.0, _WORKER_INTERVAL_S * 4))

    try:
        while True:
            try:
                registry.heartbeat(_HANDLER_ID)
                controller = getattr(app.state, "admission_controller", None)
                if drain_blocks_new_auto_claims(controller):
                    if drain_belt_fires(controller):
                        request_giw_belt_exit(reason="draining_amber_stalled")
                    controller.recheck_drain_idle()
                    await asyncio.sleep(_WORKER_INTERVAL_S)
                    continue
                job = get_queue().claim_next()
                if job is not None:
                    hb_task = asyncio.create_task(_heartbeat_while_busy(job.job_id))
                    try:
                        result = await process_job(
                            job,
                            admission_controller=controller,
                            worker_id=str(getattr(app.state, "worker_id", "") or ""),
                            worker_started_at=str(
                                getattr(app.state, "worker_boot_ts", "") or ""
                            ),
                        )
                        logger.info(
                            "cursor-auto job=%s result ok=%s terminal=%s",
                            job.job_id,
                            result.get("ok"),
                            result.get("terminal_status"),
                        )
                    except Exception as exc:
                        get_queue().mark_done(
                            job.job_id,
                            failed=True,
                            terminal_reason=format_exception_reason(exc),
                        )
                        logger.exception(
                            "cursor-auto job=%s failed: %s", job.job_id, exc
                        )
                    finally:
                        hb_task.cancel()
                        try:
                            await hb_task
                        except asyncio.CancelledError:
                            pass
                        except Exception:
                            # Re-raised from a heartbeat that already died; only
                            # CancelledError used to be caught here, so this was
                            # the escape hatch that unwound the whole loop.
                            logger.exception(
                                "cursor-auto heartbeat writer died job=%s", job.job_id
                            )
                        if controller is not None:
                            controller.recheck_drain_idle()
            except Exception:
                # Never let one iteration end the lane (hop_cadence_loop pattern).
                # CancelledError still propagates, so lifespan shutdown is intact.
                logger.exception("cursor-auto worker loop iteration failed")
            await asyncio.sleep(_WORKER_INTERVAL_S)
    finally:
        registry.unregister(_HANDLER_ID)
        logger.info("cursor-auto worker loop stopped")


async def auto_concurrent_worker_loop(app: Any) -> None:
    """Poll for concurrent-opted-in jobs; spawn each as an untracked-wait
    background task so N can run alongside each other and alongside the
    serial occupant. Production allowlist is ``lease_free_propagate`` —
    nested-scope / write-lease work stays on the serial loop.
    """
    queue = get_queue()
    while True:
        try:
            controller = getattr(app.state, "admission_controller", None)
            if drain_blocks_new_auto_claims(controller):
                if drain_belt_fires(controller):
                    request_giw_belt_exit(reason="draining_amber_stalled")
                controller.recheck_drain_idle()
                await asyncio.sleep(_CONCURRENT_POLL_INTERVAL_S)
                continue
            job = queue.claim_next_concurrent()
            if job is not None:
                emit_concurrent_claimed(
                    job_id=job.job_id,
                    thread_id=job.thread_id,
                    contract=job.contract,
                    execution_mode=job.execution_mode,
                )
                worker_id = str(getattr(app.state, "worker_id", "") or "")
                worker_started_at = str(getattr(app.state, "worker_boot_ts", "") or "")

                async def _run(job=job) -> None:
                    try:
                        await process_job(
                            job,
                            admission_controller=controller,
                            worker_id=worker_id,
                            worker_started_at=worker_started_at,
                        )
                    except Exception as exc:
                        queue.mark_done(
                            job.job_id,
                            failed=True,
                            terminal_reason=format_exception_reason(exc),
                        )
                        logger.exception(
                            "cursor-auto concurrent job=%s failed: %s",
                            job.job_id,
                            exc,
                        )

                if controller is not None:
                    controller.create_tracked_task(
                        _run(), op_id=f"cursor-auto-concurrent:{job.job_id}"
                    )
                else:
                    asyncio.create_task(_run())
        except Exception:
            logger.exception("cursor-auto concurrent worker loop iteration failed")
        await asyncio.sleep(_CONCURRENT_POLL_INTERVAL_S)


async def orphan_scanner_loop(app: Any) -> None:
    """Secondary wake: log pending queue depth (v0; full bus scan later)."""
    while True:
        snap = get_queue().snapshot()
        if snap["pending"] > 0:
            logger.info(
                "cursor-auto orphan-scanner pending=%s claimed=%s",
                snap["pending"],
                snap["claimed"],
            )
        await asyncio.sleep(_ORPHAN_INTERVAL_S)
