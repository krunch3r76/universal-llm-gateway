"""Release one claimed cursor-auto job. Not a park verb and not a GIW force."""

from __future__ import annotations

from typing import Any

from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.queue import AutoJobQueue, get_queue
from services.git_integration_worker.drain_progress import HEARTBEAT_TTL_S

logger = get_logger(__name__)

_OWNING_TASK_OPS = (
    "cursor-auto-continuity-hop:{job_id}",
    "cursor-auto-concurrent:{job_id}",
)


def _owning_op_ids(job_id: str) -> tuple[str, ...]:
    return tuple(pattern.format(job_id=job_id) for pattern in _OWNING_TASK_OPS)


def _heartbeat_inside_ttl(queue: AutoJobQueue, job_id: str) -> bool:
    """True only when a ledger age is known and still inside the stall TTL.

    No ledger (in-memory tests) is not a fresh heartbeat. Age past the TTL
    is not a reason to refuse — the row is still occupancy either way.
    """
    ledger = queue._ledger_client()
    if ledger is None:
        return False
    age = ledger.heartbeat_age_s(job_id)
    if age is None:
        return False
    return float(age) <= HEARTBEAT_TTL_S


def _live_owning_ops(controller: Any, job_id: str) -> list[str]:
    tasks = getattr(controller, "_tracked_tasks", None)
    if not tasks:
        return []
    wanted = {f"tracked-{op_id}": op_id for op_id in _owning_op_ids(job_id)}
    live: list[str] = []
    for task in list(tasks):
        op_id = wanted.get(task.get_name())
        if op_id is not None and not task.done():
            live.append(op_id)
    return live


def release_claimed_auto_job(
    job_id: str,
    *,
    reason: str,
    queue: AutoJobQueue | None = None,
    controller: Any | None = None,
    override: bool = False,
) -> dict[str, Any]:
    """Terminalize one claimed auto job and drop it from drain occupancy.

    Refuses (``http_status`` 409) while the ledger heartbeat is inside
    ``HEARTBEAT_TTL_S`` or an owning hop/concurrent tracked task is alive,
    unless ``override`` is set. A missing id is 404. A present non-claimed
    row is 409 and names ``status``. Other claimed jobs stay. This function
    does not touch park rows and does not force-restart GIW.
    """
    target = (job_id or "").strip()
    recorded = (reason or "").strip() or "release_claimed_auto_job"
    q = queue or get_queue()
    job = q.get(target)
    if job is None:
        return {
            "ok": False,
            "job_id": target,
            "reason": "not_found",
            "status": None,
            "http_status": 404,
        }
    if job.status != "claimed":
        return {
            "ok": False,
            "job_id": target,
            "reason": "not_claimed",
            "status": job.status,
            "http_status": 409,
        }
    fresh = _heartbeat_inside_ttl(q, target)
    live_ops = _live_owning_ops(controller, target) if controller is not None else []
    if not override and (fresh or live_ops):
        refusal = "tracked_task_alive" if live_ops else "heartbeat_inside_ttl"
        return {
            "ok": False,
            "job_id": target,
            "reason": refusal,
            "status": job.status,
            "http_status": 409,
            "heartbeat_inside_ttl": fresh,
            "live_tasks": live_ops,
        }
    q.mark_done(target, failed=True, terminal_reason=recorded)
    cancelled: list[str] = []
    if controller is not None:
        cancel = getattr(controller, "cancel_tracked_task", None)
        if cancel is not None:
            for op_id in _owning_op_ids(target):
                if cancel(op_id):
                    cancelled.append(op_id)
        recheck = getattr(controller, "recheck_drain_idle", None)
        if recheck is not None:
            recheck()
    logger.info(
        "released claimed cursor-auto job=%s reason=%s override=%s cancelled=%s",
        target,
        recorded,
        override,
        cancelled,
    )
    return {
        "ok": True,
        "job_id": target,
        "status": "failed",
        "terminal_reason": recorded,
        "http_status": 200,
        "cancelled_tasks": cancelled,
    }
