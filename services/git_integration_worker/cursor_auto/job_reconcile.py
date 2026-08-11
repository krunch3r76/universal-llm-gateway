"""Startup/shutdown reconcile for open cursor-auto jobs (queue-owner restart)."""

from __future__ import annotations

import asyncio
from typing import Any

from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.closeout_replay import (
    is_never_dispatched,
    job_has_pending_outbox,
    job_should_skip_loss_report,
)
from services.git_integration_worker.cursor_auto.handler_terminal import (
    post_queue_owner_restart_terminal,
)
from services.git_integration_worker.cursor_auto.job_ledger import (
    TERMINAL_REASON_QUEUE_OWNER_RESTART,
    AutoJobLedger,
    get_ledger,
)
from services.git_integration_worker.cursor_auto.terminal_reason_codec import (
    TERMINAL_REASON_RECONCILE_INFLIGHT_LOST,
)
from services.git_integration_worker.cursor_auto.queue import (
    AutoJob,
    AutoJobQueue,
    get_queue,
)
from services.git_integration_worker.cursor_auto.silence_visibility_events import (
    emit_queue_owner_restart_bus_unposted,
)
from services.git_integration_worker.cursor_bus import CursorBusClient

logger = get_logger(__name__)

_SHUTDOWN_TIMEOUT_S = 5.0


def _bus_post_succeeded(post_result: dict[str, Any] | None) -> bool:
    """True when the restart terminal reply reached the bus (HTTP < 400)."""
    if not isinstance(post_result, dict):
        return False
    code = post_result.get("status_code")
    return isinstance(code, int) and code < 400


def _mark_bus_notify_pending(
    ledger: AutoJobLedger,
    job: AutoJob,
    *,
    status_code: int | None,
) -> None:
    """Durable + event mark when ledger death has no waiter-visible bus turn."""
    ledger.merge_record_json(
        job.job_id,
        {
            "bus_notify_pending": True,
            "bus_notify_mark": "queue_owner_restart_death",
        },
    )
    emit_queue_owner_restart_bus_unposted(
        job_id=job.job_id,
        thread_id=str(job.thread_id or ""),
        status_code=status_code,
    )


def _open_jobs_union(
    queue: AutoJobQueue, ledger: AutoJobLedger
) -> list[AutoJob]:
    seen: set[str] = set()
    merged: list[AutoJob] = []
    for job in ledger.list_open():
        if job.job_id not in seen:
            seen.add(job.job_id)
            merged.append(job)
    for job in queue.list_open_jobs():
        if job.job_id not in seen:
            seen.add(job.job_id)
            merged.append(job)
    return merged


async def _terminalize_job(
    job: AutoJob,
    *,
    queue: AutoJobQueue,
    ledger: AutoJobLedger,
    client: CursorBusClient,
    post_bus: bool,
) -> AutoJob | None:
    if job_has_pending_outbox(job.job_id):
        return None
    if job_should_skip_loss_report(job.job_id):
        terminal = ledger.mark_terminal(job.job_id, status="done", terminal_reason=None)
        if terminal is None:
            return None
        queue.mark_done(job.job_id, failed=False)
        return terminal
    if not is_never_dispatched(job.job_id):
        terminal = ledger.mark_terminal(
            job.job_id,
            status="failed",
            terminal_reason=TERMINAL_REASON_RECONCILE_INFLIGHT_LOST,
        )
        if terminal is None:
            return None
        queue.mark_done(
            job.job_id,
            failed=True,
            terminal_reason=TERMINAL_REASON_RECONCILE_INFLIGHT_LOST,
        )
        return terminal
    terminal = ledger.mark_terminal(
        job.job_id,
        status="failed",
        terminal_reason=TERMINAL_REASON_QUEUE_OWNER_RESTART,
    )
    if terminal is None:
        return None
    queue.mark_done(
        job.job_id,
        failed=True,
        terminal_reason=TERMINAL_REASON_QUEUE_OWNER_RESTART,
    )
    if post_bus and terminal.thread_id:
        post_result: dict[str, Any] | None = None
        try:
            post_result = await post_queue_owner_restart_terminal(
                terminal,
                client=client,
                queue=queue,
            )
        except Exception as exc:
            logger.warning(
                "cursor-auto restart terminal bus post failed job=%s: %s",
                job.job_id,
                exc,
            )
            _mark_bus_notify_pending(ledger, terminal, status_code=None)
            return terminal
        if not _bus_post_succeeded(post_result):
            code = (
                post_result.get("status_code")
                if isinstance(post_result, dict)
                else None
            )
            _mark_bus_notify_pending(
                ledger,
                terminal,
                status_code=code if isinstance(code, int) else None,
            )
    return terminal


async def reconcile_open_auto_jobs(
    *,
    post_bus: bool = True,
    reason: str = TERMINAL_REASON_QUEUE_OWNER_RESTART,
) -> list[AutoJob]:
    """Terminalize every open job; optional bus notify for waiters."""
    del reason  # v1 uses fixed enum; reserved for future alias mapping
    queue = get_queue()
    ledger = get_ledger()
    client = CursorBusClient()
    terminalized: list[AutoJob] = []
    for job in _open_jobs_union(queue, ledger):
        row = await _terminalize_job(
            job,
            queue=queue,
            ledger=ledger,
            client=client,
            post_bus=post_bus,
        )
        if row is not None:
            terminalized.append(row)
    if terminalized:
        logger.warning(
            "cursor-auto reconcile terminalized %d open job(s) reason=%s",
            len(terminalized),
            TERMINAL_REASON_QUEUE_OWNER_RESTART,
        )
    return terminalized


async def startup_auto_job_reconcile(app: Any) -> None:
    """Backstop for SIGKILL: terminalize durable open rows before enqueue."""
    del app
    await reconcile_open_auto_jobs(post_bus=True)


async def shutdown_auto_jobs(app: Any, *, timeout_s: float = _SHUTDOWN_TIMEOUT_S) -> None:
    """Best-effort terminalize before worker cancel (bounded wait)."""
    del app
    try:
        await asyncio.wait_for(
            reconcile_open_auto_jobs(post_bus=True),
            timeout=timeout_s,
        )
    except TimeoutError:
        logger.warning(
            "cursor-auto shutdown reconcile timed out after %.1fs", timeout_s
        )
