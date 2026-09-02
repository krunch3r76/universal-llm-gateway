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
    post_queue_owner_restart_recovered,
    post_queue_owner_restart_terminal,
)
from services.git_integration_worker.cursor_auto.job_ledger import (
    TERMINAL_REASON_QUEUE_OWNER_RESTART,
    AutoJobLedger,
    get_ledger,
)
from services.git_integration_worker.cursor_auto.job_reconcile_honor import (
    honor_claimed_dispatched_job,
)
from services.git_integration_worker.cursor_auto.queue import (
    AutoJob,
    AutoJobQueue,
    get_queue,
)
from services.git_integration_worker.cursor_auto.reconcile_visibility_events import (
    emit_reconcile_rehydrate_exhausted,
    emit_reconcile_rehydrated,
    emit_reconcile_superseded,
)
from services.git_integration_worker.cursor_auto.silence_visibility_events import (
    emit_queue_owner_restart_bus_unposted,
)
from services.git_integration_worker.cursor_auto.terminal_reason_codec import (
    TERMINAL_REASON_RESTART_RECONCILE_SUPERSEDED,
)
from services.git_integration_worker.cursor_bus import CursorBusClient

logger = get_logger(__name__)

_SHUTDOWN_TIMEOUT_S = 5.0
_REHYDRATE_GENERATION_CAP = 3


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


async def _reconcile_queued_job(
    job: AutoJob,
    *,
    queue: AutoJobQueue,
    ledger: AutoJobLedger,
    client: CursorBusClient,
    post_bus: bool,
    rehydrate: bool,
    restart_intent_id: str | None,
) -> AutoJob | None:
    """Disposition for a ledger row whose status is still "queued".

    ``rehydrate=False`` (shutdown call site): the row is already durable
    from its original ``enqueue()`` insert — do nothing destructive. If a
    drain ``intent_id`` is known, stamp it as best-effort provenance for the
    startup rehydrate to inherit; otherwise no-op.

    ``rehydrate=True`` (startup call site): this is the real recovery path
    — S-2(ii) successor gate, then generation cap, then requeue.
    """
    if not rehydrate:
        if restart_intent_id:
            ledger.merge_record_json(
                job.job_id, {"restart_intent_id": restart_intent_id}
            )
        return None

    record = ledger.read_record_json(job.job_id)
    generation = int(record.get("generation") or 0)
    inherited_intent_id = restart_intent_id or record.get("restart_intent_id")

    successor = ledger.successor_job_for_thread_turn(job.thread_id, job.turn_number)
    if successor is not None:
        ledger.merge_record_json(
            job.job_id,
            {
                "rehydrated": True,
                "generation": generation,
                "rehydrate_superseded_by": successor.job_id,
            },
        )
        terminal = ledger.mark_terminal(
            job.job_id,
            status="superseded",
            terminal_reason=TERMINAL_REASON_RESTART_RECONCILE_SUPERSEDED,
        )
        if terminal is None:
            return None
        queue.mark_done(
            job.job_id,
            failed=True,
            terminal_reason=TERMINAL_REASON_RESTART_RECONCILE_SUPERSEDED,
        )
        emit_reconcile_superseded(
            job_id=job.job_id,
            thread_id=str(job.thread_id or ""),
            successor_job_id=successor.job_id,
            generation=generation,
        )
        return terminal

    next_generation = generation + 1
    if next_generation > _REHYDRATE_GENERATION_CAP:
        ledger.merge_record_json(
            job.job_id,
            {"rehydrated": True, "generation": generation, "rehydrate_exhausted": True},
        )
        terminal = ledger.mark_terminal(
            job.job_id,
            status="failed",
            terminal_reason=TERMINAL_REASON_QUEUE_OWNER_RESTART,
        )
        if terminal is None:
            return None
        queue.mark_done(
            job.job_id, failed=True, terminal_reason=TERMINAL_REASON_QUEUE_OWNER_RESTART
        )
        emit_reconcile_rehydrate_exhausted(
            job_id=job.job_id,
            thread_id=str(job.thread_id or ""),
            generation=generation,
        )
        if post_bus and terminal.thread_id:
            try:
                await post_queue_owner_restart_terminal(
                    terminal,
                    client=client,
                    queue=queue,
                    rehydrate_generation=generation,
                )
            except Exception as exc:
                logger.warning(
                    "cursor-auto rehydrate-exhausted terminal bus post failed job=%s: %s",
                    job.job_id,
                    exc,
                )
                _mark_bus_notify_pending(ledger, terminal, status_code=None)
        return terminal

    ledger.merge_record_json(
        job.job_id,
        {
            "rehydrated": True,
            "generation": next_generation,
            "restart_intent_id": inherited_intent_id,
        },
    )
    queue.requeue_rehydrated(job)
    emit_reconcile_rehydrated(
        job_id=job.job_id,
        thread_id=str(job.thread_id or ""),
        generation=next_generation,
        restart_intent_id=inherited_intent_id,
    )
    if post_bus and job.thread_id:
        try:
            await post_queue_owner_restart_recovered(
                job, client=client, generation=next_generation
            )
        except Exception as exc:
            logger.warning(
                "cursor-auto rehydrate-recovered notify failed job=%s: %s",
                job.job_id,
                exc,
            )
    return None


async def _terminalize_job(
    job: AutoJob,
    *,
    queue: AutoJobQueue,
    ledger: AutoJobLedger,
    client: CursorBusClient,
    post_bus: bool,
    rehydrate: bool = False,
    restart_intent_id: str | None = None,
) -> AutoJob | None:
    if job.status == "queued":
        return await _reconcile_queued_job(
            job,
            queue=queue,
            ledger=ledger,
            client=client,
            post_bus=post_bus,
            rehydrate=rehydrate,
            restart_intent_id=restart_intent_id,
        )
    if job_has_pending_outbox(job.job_id):
        return None
    if job_should_skip_loss_report(job.job_id):
        terminal = ledger.mark_terminal(job.job_id, status="done", terminal_reason=None)
        if terminal is None:
            return None
        queue.mark_done(job.job_id, failed=False)
        return terminal
    if not is_never_dispatched(job.job_id):
        if not rehydrate:
            return None
        return await honor_claimed_dispatched_job(
            job,
            queue=queue,
            ledger=ledger,
            client=client,
            post_bus=post_bus,
        )
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
    rehydrate: bool = False,
    restart_intent_id: str | None = None,
) -> list[AutoJob]:
    """Terminalize every open claimed-never-dispatched job; keep (and at
    startup, rehydrate) every open queued-never-claimed job. A claimed
    *and* dispatched job is left alone at shutdown (``rehydrate=False``) and,
    at startup, is honor-consulted against the bus before being terminalized
    (see ``honor_claimed_dispatched_job``) rather than terminalized outright.
    Optional bus notify for waiters on every terminal path, including honor's
    ``reconcile_inflight_lost``."""
    del reason
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
            rehydrate=rehydrate,
            restart_intent_id=restart_intent_id,
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
    """Backstop for SIGKILL: terminalize durable open+claimed rows, REHYDRATE
    durable open+queued rows, before enqueue resumes."""
    del app
    await reconcile_open_auto_jobs(post_bus=True, rehydrate=True)


async def shutdown_auto_jobs(app: Any, *, timeout_s: float = _SHUTDOWN_TIMEOUT_S) -> None:
    """Best-effort terminalize claimed rows before worker cancel (bounded
    wait). Queued rows are left durable — NOT rehydrated here (that only
    happens at the next startup) — but if this shutdown is drain-triggered,
    stamp the drain's intent_id onto them as provenance for that rehydrate.
    """
    controller = getattr(app.state, "admission_controller", None)
    intent_id = None
    if controller is not None:
        intent_id = controller.drain_state().get("intent_id")
    try:
        await asyncio.wait_for(
            reconcile_open_auto_jobs(
                post_bus=True, rehydrate=False, restart_intent_id=intent_id
            ),
            timeout=timeout_s,
        )
    except TimeoutError:
        logger.warning(
            "cursor-auto shutdown reconcile timed out after %.1fs", timeout_s
        )
