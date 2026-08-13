"""Deliver supersede negative lifecycle to the displaced seat's lane."""

from __future__ import annotations

from typing import Any

from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.cse_wake_delivery import (
    deliver_cse_wake,
    is_chat_delivery_capable,
)
from services.git_integration_worker.cursor_auto.dispatch_job_superseded_events import (
    emit_dispatch_job_superseded_notify,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob
from services.git_integration_worker.cursor_auto.supersede import (
    post_superseded_terminal,
)

logger = get_logger(__name__)


def build_superseded_notify_prompt(
    *,
    superseded_job_id: str,
    superseding_job_id: str,
    method: str,
    reason: str,
    thread_id: str,
) -> str:
    """In-chat supersede notice — token-free, not a CLOSEOUT copy."""
    return (
        "Dispatch superseded (negative lifecycle).\n"
        f"superseded_job_id: {superseded_job_id}\n"
        f"superseding_job_id: {superseding_job_id}\n"
        f"method: {method}\n"
        f"reason: {reason}\n"
        f"thread_id: {thread_id}\n"
        "\n"
        "Poll may complete on status:superseded — do not wait for status:done."
    )


async def notify_superseded_seat(
    old_job: AutoJob,
    new_job: AutoJob,
    *,
    mark: dict[str, Any],
    client: Any,
    queue: Any,
    dispatch_id: str | None,
    post_bus_terminal: bool,
) -> dict[str, Any]:
    """Emit event, optional bus terminal, and in-chat delivery for the loser."""
    method = str(mark.get("method") or "pre_register_live_run")
    reason = str(mark.get("reason") or "")
    emit_dispatch_job_superseded_notify(
        superseded_job_id=old_job.job_id,
        superseding_job_id=new_job.job_id,
        method=method,
        reason=reason,
        thread_id=str(old_job.thread_id),
        superseded_dispatch_id=dispatch_id,
    )

    terminal: dict[str, Any] | None = None
    if post_bus_terminal:
        terminal = await post_superseded_terminal(
            old_job,
            client=client,
            queue=queue,
            dispatch_id=dispatch_id,
        )

    delivery: dict[str, Any] = {"ok": False, "skipped": True}
    if is_chat_delivery_capable(old_job.from_agent):
        delivery = deliver_cse_wake(
            chat_url=old_job.cse_chat_url,
            registration_id=old_job.cse_registration_id,
            prompt_text=build_superseded_notify_prompt(
                superseded_job_id=old_job.job_id,
                superseding_job_id=new_job.job_id,
                method=method,
                reason=reason,
                thread_id=str(old_job.thread_id),
            ),
        )

    return {
        "event": "dispatch.job.superseded.notify",
        "terminal": terminal,
        "delivery": delivery,
    }


__all__ = [
    "build_superseded_notify_prompt",
    "notify_superseded_seat",
]
