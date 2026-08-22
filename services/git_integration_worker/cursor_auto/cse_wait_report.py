"""Retired mid-wait CSE paste — admit still calls the scheduler; it no-ops.

A ``TYPE: WAITING`` followup starts a generate. Tab ⟂ session means that
paste is keepalive churn. Completion paging (``cse_pager_resolve`` +
Park-on-WAKE) replaced it. ``should_emit_wait_report`` is permanently false.
"""

from __future__ import annotations

import asyncio
from typing import Any

from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.cse_wake_delivery import (
    HttpPoster,
    deliver_cse_wake,
    is_chat_delivery_capable,
)
from services.git_integration_worker.cursor_auto.execution_mode import (
    is_concurrent_execution_mode,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob, AutoJobQueue

_DEFAULT_TIMEOUT_S = 60.0
logger = get_logger(__name__)


def build_wait_report_prompt_text(
    *,
    waiting_on: str,
    queue_position: int | None = None,
    occupant: str | None = None,
    job_id: str | None = None,
    thread_id: str | None = None,
) -> str:
    """Token-free WAITING body: park; do not stream, poll, hop, or re-issue."""
    lines = ["TYPE: WAITING", f"waiting_on: {waiting_on.strip()}"]
    if queue_position is not None:
        lines.append(f"queue_position: {queue_position}")
    if occupant:
        lines.append(f"occupant: {occupant.strip()}")
    if job_id:
        lines.append(f"job_id: {job_id.strip()}")
    if thread_id:
        lines.append(f"thread: {thread_id.strip()}")
    lines.extend(
        [
            "action: TYPE: PARKED",
            "",
            "Do not stream, poll, hop-refresh, or re-issue.",
            "Reply TYPE: PARKED including waiting_on with the same value.",
            "Then wait for TYPE: WAKE or a WAITING update.",
        ]
    )
    return "\n".join(lines)


def deliver_cse_wait_report(
    *,
    chat_url: str | None,
    registration_id: str | None,
    prompt_text: str,
    purpose: str = "operator-proxy",
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    post: HttpPoster | None = None,
) -> dict[str, Any]:
    """POST followup that leaves the operator CSE attached after paste."""
    return deliver_cse_wake(
        chat_url=chat_url,
        registration_id=registration_id,
        prompt_text=prompt_text,
        purpose=purpose,
        timeout_s=timeout_s,
        post=post,
        reattach=False,
        retain_lane=True,
    )


async def maybe_deliver_cse_wait_report(
    job: AutoJob,
    *,
    waiting_on: str,
    queue_position: int | None = None,
    occupant: str | None = None,
    post: HttpPoster | None = None,
    chat_url: str | None = None,
    registration_id: str | None = None,
) -> dict[str, Any]:
    """Skip IDE-class or missing identity; otherwise paste WAITING on the CSE."""
    if not is_chat_delivery_capable(job.from_agent):
        return {"ok": False, "skipped": True, "reason": "not_chat_delivery_capable"}

    if chat_url is None and registration_id is None:
        from services.git_integration_worker.cursor_auto.cse_pager_resolve import (
            resolve_live_cse_address,
        )

        live = resolve_live_cse_address(job)
        chat_url = live.get("chat_url")
        registration_id = live.get("registration_id")
    else:
        chat_url = chat_url or getattr(job, "cse_chat_url", None)
        registration_id = registration_id or getattr(job, "cse_registration_id", None)
    prompt = build_wait_report_prompt_text(
        waiting_on=waiting_on,
        queue_position=queue_position,
        occupant=occupant,
        job_id=job.job_id,
        thread_id=str(job.thread_id),
    )
    return await asyncio.to_thread(
        deliver_cse_wait_report,
        chat_url=chat_url,
        registration_id=registration_id,
        prompt_text=prompt,
        post=post,
    )


def serial_queue_occupant(
    queue: AutoJobQueue,
    *,
    exclude_job_id: str | None = None,
) -> AutoJob | None:
    """Claimed serial Auto job the waiter is behind — hops and concurrent modes skip."""
    for job in queue.list_open_jobs():
        if exclude_job_id and job.job_id == exclude_job_id:
            continue
        if job.status != "claimed":
            continue
        if job.continuity_hop:
            continue
        if is_concurrent_execution_mode(job.execution_mode):
            continue
        return job
    return None


def should_emit_wait_report(
    job: AutoJob,
    *,
    occupied: bool,
    queue_position: int | None,
) -> bool:
    """False. Mid-wait WAITING paste was replaced by completion paging.

    ``occupied`` / ``queue_position`` stay on the signature so admit can
    still call through; they must not mint a CSE followup.
    """
    _ = (job, occupied, queue_position)
    return False


def schedule_wait_report_if_waiting(
    job: AutoJob,
    *,
    queue: AutoJobQueue,
    waiter: dict[str, Any],
    controller: Any | None = None,
    post: HttpPoster | None = None,
) -> bool:
    """Stamp the watch and background-paste WAITING. Never blocks admit."""
    occupant = serial_queue_occupant(queue, exclude_job_id=job.job_id)
    position = waiter.get("queue_position")
    if isinstance(position, int):
        queue_position: int | None = position
    else:
        queue_position = None
    if not should_emit_wait_report(
        job, occupied=occupant is not None, queue_position=queue_position
    ):
        return False
    from services.git_integration_worker.cursor_auto.hop_cadence_home_lane import (
        watch_thread_for_job,
    )
    from services.git_integration_worker.cursor_auto.hop_cadence_waiting import (
        mark_watch_wait_report,
    )

    mark_watch_wait_report(watch_thread_for_job(job), job.job_id)
    occupant_id = occupant.job_id if occupant is not None else None
    coro = maybe_deliver_cse_wait_report(
        job,
        waiting_on="cursor-auto serial queue",
        queue_position=queue_position,
        occupant=occupant_id,
        post=post,
    )
    op_id = f"cursor-auto-wait-report:{job.job_id}"
    if controller is not None and hasattr(controller, "create_tracked_task"):
        controller.create_tracked_task(coro, op_id=op_id)
        return True
    try:
        asyncio.get_running_loop().create_task(coro, name=op_id)
    except RuntimeError:
        logger.warning("wait-report skipped; no running loop job=%s", job.job_id)
        return False
    return True


__all__ = [
    "build_wait_report_prompt_text",
    "deliver_cse_wait_report",
    "maybe_deliver_cse_wait_report",
    "schedule_wait_report_if_waiting",
    "serial_queue_occupant",
    "should_emit_wait_report",
]
