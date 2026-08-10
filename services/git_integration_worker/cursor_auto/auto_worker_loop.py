"""Background worker loops for the cursor-auto admit lane."""

from __future__ import annotations

import asyncio
from typing import Any

from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.handler import process_job
from services.git_integration_worker.cursor_auto.liveness import get_registry
from services.git_integration_worker.cursor_auto.queue import get_queue

logger = get_logger(__name__)

_HANDLER_ID = "cursor-auto-primary"
_WORKER_INTERVAL_S = 0.5
_ORPHAN_INTERVAL_S = 15.0


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
                job = get_queue().claim_next()
                if job is not None:
                    hb_task = asyncio.create_task(_heartbeat_while_busy(job.job_id))
                    try:
                        controller = getattr(app.state, "admission_controller", None)
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
                        get_queue().mark_done(job.job_id, failed=True)
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
            except Exception:
                # Never let one iteration end the lane (hop_cadence_loop pattern).
                # CancelledError still propagates, so lifespan shutdown is intact.
                logger.exception("cursor-auto worker loop iteration failed")
            await asyncio.sleep(_WORKER_INTERVAL_S)
    finally:
        registry.unregister(_HANDLER_ID)
        logger.info("cursor-auto worker loop stopped")


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
