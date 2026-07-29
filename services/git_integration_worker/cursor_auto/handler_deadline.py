"""Job-TTL gate — refuse stale intent before Auto spends anything on it.

Splits the ``deadline:`` decision out of ``handler.py``: parse the field, emit
the observation, and post the terminal turn. Returning ``None`` means the job may
proceed (no deadline declared, or the declared one has not passed).
"""

from __future__ import annotations

from typing import Any

from services.git_integration_worker.cursor_auto.execute_events import emit_job_expired
from services.git_integration_worker.cursor_auto.fix_hints import (
    DEADLINE_UNPARSEABLE_FIX_HINT,
)
from services.git_integration_worker.cursor_auto.handler_terminal import (
    post_terminal_status,
    terminal_expired,
)
from services.git_integration_worker.cursor_auto.job_deadline import deadline_verdict
from services.git_integration_worker.cursor_auto.queue import AutoJob
from services.git_integration_worker.cursor_bus import CursorBusClient


async def deadline_terminal(
    job: AutoJob,
    *,
    client: CursorBusClient,
    queue: Any,
) -> dict[str, Any] | None:
    """Terminate an expired or unparseable-deadline job; ``None`` to proceed."""
    verdict = deadline_verdict(job.body, enqueued_at=job.enqueued_at)
    if not verdict.blocking:
        return None
    if verdict.state == "expired":
        emit_job_expired(
            thread_id=job.thread_id,
            deadline=str(verdict.raw),
            elapsed_s=verdict.elapsed_s,
        )
        return await terminal_expired(
            job,
            client=client,
            queue=queue,
            deadline=verdict.raw,
            elapsed_s=verdict.elapsed_s,
        )
    summary = (
        f"DIRECTIVE deadline: {verdict.raw!r} is not a parseable TTL — a "
        "deadline that silently does nothing is worse than no deadline."
    )
    return await post_terminal_status(
        job,
        client=client,
        queue=queue,
        summary=summary,
        disposition="blocked",
        contract=job.contract,
        terminal_status="status:blocked",
        payload={
            "summary": summary,
            "reason": "deadline_unparseable",
            "provided": verdict.raw,
            "fix_hint": DEADLINE_UNPARSEABLE_FIX_HINT,
        },
        failed=True,
    )


__all__ = ["deadline_terminal"]
