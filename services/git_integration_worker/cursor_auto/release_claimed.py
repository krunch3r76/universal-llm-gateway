"""Release one claimed cursor-auto job. Not a park verb and not a GIW force."""

from __future__ import annotations

from typing import Any

from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.queue import AutoJobQueue, get_queue

logger = get_logger(__name__)

_HOP_TASK_OP = "cursor-auto-continuity-hop:{job_id}"


def release_claimed_auto_job(
    job_id: str,
    *,
    reason: str,
    queue: AutoJobQueue | None = None,
    controller: Any | None = None,
) -> dict[str, Any]:
    """Terminalize one claimed auto job and drop it from drain occupancy.

    Other claimed jobs stay. ``cancel_discard`` remains the park verb — this
    function does not touch park rows and does not force-restart GIW.
    """
    target = (job_id or "").strip()
    recorded = (reason or "").strip() or "release_claimed_auto_job"
    q = queue or get_queue()
    job = q.get(target)
    if job is None or job.status != "claimed":
        return {
            "ok": False,
            "job_id": target,
            "reason": "not_claimed",
            "status": None if job is None else job.status,
        }
    q.mark_done(target, failed=True, terminal_reason=recorded)
    cancelled_task = False
    if controller is not None:
        cancel = getattr(controller, "cancel_tracked_task", None)
        if cancel is not None:
            cancelled_task = bool(cancel(_HOP_TASK_OP.format(job_id=target)))
        recheck = getattr(controller, "recheck_drain_idle", None)
        if recheck is not None:
            recheck()
    logger.info(
        "released claimed cursor-auto job=%s reason=%s cancelled_task=%s",
        target,
        recorded,
        cancelled_task,
    )
    return {
        "ok": True,
        "job_id": target,
        "status": "failed",
        "terminal_reason": recorded,
        "cancelled_task": cancelled_task,
    }
