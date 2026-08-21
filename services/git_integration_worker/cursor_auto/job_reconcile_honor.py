"""Startup honor consult for claimed+dispatched cursor-auto jobs after GIW restart.

Pages agent-bus turns from the request turn forward (not the AC-8 ``last=8``
window) to decide whether a closeout already exists before stamping
``reconcile_inflight_lost``.
"""

from __future__ import annotations

from typing import Any

from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.closeout_bus_scan import (
    fetch_turns_from,
    find_closeout_for_dispatch,
)
from services.git_integration_worker.cursor_auto.closeout_outbox_events import (
    emit_closeout_replay_suppressed_loss_report,
)
from services.git_integration_worker.cursor_auto.closeout_replay import (
    relay_phase_for_job,
)
from services.git_integration_worker.cursor_auto.job_ledger import AutoJobLedger
from services.git_integration_worker.cursor_auto.queue import AutoJob, AutoJobQueue
from services.git_integration_worker.cursor_auto.reconcile_visibility_events import (
    emit_reconcile_inflight_lost,
)
from services.git_integration_worker.cursor_auto.terminal_reason_codec import (
    TERMINAL_REASON_RECONCILE_INFLIGHT_LOST,
)

logger = get_logger(__name__)


def find_sdk_turn_for_dispatch(
    turns: list[dict[str, Any]],
    *,
    dispatch_id: str,
) -> dict[str, Any] | None:
    """Return the first cursor-sdk turn whose subject or body names ``dispatch_id``."""
    for turn in turns:
        if turn.get("from") != "cursor-sdk":
            continue
        subject = str(turn.get("subject") or "")
        body = str(turn.get("body") or "")
        if dispatch_id in subject or dispatch_id in body:
            return turn
    return None


def _mark_honored_done(
    job: AutoJob,
    *,
    queue: AutoJobQueue,
    ledger: AutoJobLedger,
    dispatch_id: str,
    thread_id: str,
) -> AutoJob | None:
    """Terminalize as done when bus scan finds an existing closeout."""
    terminal = ledger.mark_terminal(job.job_id, status="done", terminal_reason=None)
    if terminal is None:
        return None
    queue.mark_done(job.job_id, failed=False)
    emit_closeout_replay_suppressed_loss_report(
        dispatch_id=dispatch_id,
        job_id=job.job_id,
        thread_id=thread_id,
    )
    logger.info(
        "cursor-auto honor consult marked done job=%s dispatch=%s",
        job.job_id,
        dispatch_id,
    )
    return terminal


async def honor_claimed_dispatched_job(
    job: AutoJob,
    *,
    queue: AutoJobQueue,
    ledger: AutoJobLedger,
) -> AutoJob | None:
    """Consult the bus at startup for a closeout before inflight-lost terminalize.

    Returns ``None`` when the bus is unreachable so the row stays claimed for a
    later boot. Honors cursor-auto ``TYPE: CLOSEOUT`` and cursor-sdk turns that
    name the dispatch; otherwise stamps ``reconcile_inflight_lost``.
    """
    dispatch_id, _phase = relay_phase_for_job(job.job_id)
    if not dispatch_id:
        return None
    thread_id = str(job.thread_id or "")
    after_turn = int(job.turn_number or 0)
    turns, scan_err = await fetch_turns_from(thread_id, after_turn=after_turn)
    if turns is None:
        logger.warning(
            "cursor-auto honor consult deferred job=%s dispatch=%s reason=%s",
            job.job_id,
            dispatch_id,
            scan_err or "bus_unreachable",
        )
        return None

    if find_closeout_for_dispatch(turns, dispatch_id=dispatch_id):
        return _mark_honored_done(
            job,
            queue=queue,
            ledger=ledger,
            dispatch_id=dispatch_id,
            thread_id=thread_id,
        )

    if find_sdk_turn_for_dispatch(turns, dispatch_id=dispatch_id):
        return _mark_honored_done(
            job,
            queue=queue,
            ledger=ledger,
            dispatch_id=dispatch_id,
            thread_id=thread_id,
        )

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
    emit_reconcile_inflight_lost(
        job_id=job.job_id,
        thread_id=thread_id,
        dispatch_id=dispatch_id,
    )
    logger.warning(
        "cursor-auto honor consult inflight_lost job=%s dispatch=%s",
        job.job_id,
        dispatch_id,
    )
    return terminal
