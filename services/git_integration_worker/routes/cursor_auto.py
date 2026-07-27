"""HTTP routes for Cursor Auto admit path (enqueue + liveness + worker loop)."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.handler import process_job
from services.git_integration_worker.cursor_auto.liveness import get_registry
from services.git_integration_worker.cursor_auto.queue import get_queue
from services.git_integration_worker.cursor_auto.supersede import (
    supersede_same_thread_inflight,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/git/cursor-auto", tags=["cursor-auto"])

_HANDLER_ID = "cursor-auto-primary"
_WORKER_INTERVAL_S = 0.5
_ORPHAN_INTERVAL_S = 15.0


class EnqueueBody(BaseModel):
    """Payload from MCP ``agent_bus.request`` after turn write."""

    model_config = ConfigDict(extra="forbid")

    thread_id: str
    turn_number: int = Field(ge=1)
    subject: str
    body: str = ""
    from_agent: str
    to_agent: str = "cursor"
    desired_model: str = "auto"
    desired_effort: str = "medium"
    contract: str = "answer"
    require_attended: bool = False


@router.get("/liveness")
async def liveness() -> dict[str, Any]:
    """Arm-predicate probe — live iff ≥1 Auto handler heartbeat is fresh."""
    return get_registry().snapshot()


@router.get("/queue")
async def queue_snapshot() -> dict[str, Any]:
    return get_queue().snapshot()


@router.post("/enqueue")
async def enqueue(body: EnqueueBody):
    """Admit-on-request enqueue. Requires a live Auto handler (else 503)."""
    registry = get_registry()
    if not registry.is_live():
        return JSONResponse(
            status_code=503,
            content={
                "ok": False,
                "handler_status": "no-auto-handler",
                "reason": "no_live_auto_handler",
                "liveness": registry.snapshot(),
            },
        )
    queue = get_queue()
    job = queue.enqueue(
        thread_id=body.thread_id,
        turn_number=body.turn_number,
        subject=body.subject,
        body=body.body,
        from_agent=body.from_agent,
        to_agent=body.to_agent,
        desired_model=body.desired_model,
        desired_effort=body.desired_effort,
        contract=body.contract,
        require_attended=body.require_attended,
    )
    logger.info(
        "cursor-auto enqueued job=%s thread=%s turn=%s",
        job.job_id,
        body.thread_id,
        body.turn_number,
    )
    # A second request on a private thread is a backtrack, not a queue append:
    # interrupt the live episode so the new DIRECTIVE does not wait it out.
    interrupt = await supersede_same_thread_inflight(job, queue=queue)
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "handler_status": "auto-admit-armed",
            "job_id": job.job_id,
            "superseded": interrupt,
            "queue": queue.snapshot(),
        },
    )


async def auto_worker_loop(app: Any) -> None:
    """Background: heartbeat + claim/process queued Auto jobs.

    Heartbeat must continue during long ``process_job`` awaits (nested SDK
    often exceeds ``heartbeat_ttl_s``). Otherwise enqueue sees a dead handler
    mid-job and the next ``agent_bus.request`` 503s — 5867 DIRECTIVE-4 class.
    """
    registry = get_registry()
    registry.register(_HANDLER_ID)
    logger.info("cursor-auto worker loop started handler_id=%s", _HANDLER_ID)

    async def _heartbeat_while_busy() -> None:
        while True:
            registry.heartbeat(_HANDLER_ID)
            await asyncio.sleep(min(5.0, _WORKER_INTERVAL_S * 4))

    try:
        while True:
            registry.heartbeat(_HANDLER_ID)
            job = get_queue().claim_next()
            if job is not None:
                hb_task = asyncio.create_task(_heartbeat_while_busy())
                try:
                    result = await process_job(job)
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
                    with suppress(asyncio.CancelledError):
                        await hb_task
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
