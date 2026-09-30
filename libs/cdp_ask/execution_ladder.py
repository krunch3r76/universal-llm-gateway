"""Execution finish ladder shared by first-run and resumed executions.

``finish_execution`` turns a ``run_execution`` payload into store transitions
(``awaiting_wake`` / ``completed`` / ``failed`` / ``aborted``), each of which
writes through to the registry row's ``execution_state``. ``guard_execution``
is the task boundary: any exception a run or resume escapes with becomes an
explicit terminal, so no execution is left ``running`` in memory with a row
still in flight.

One branch is new (a:36969 / a:36948 review note): a failure whose error reads
as the Chrome host dying under a live turn — while the row still has its
``chat_url`` — is not a terminal. The row is parked ``dormant`` and the record
goes to ``execution_resume`` with trigger ``host_lost``; the session at that
URL is where the answer is still being written. One resume per execution:
a second host loss terminalizes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from claude_bundles import cdp_registry

from cdp_ask.execution_store import ExecutionStore
from cdp_ask.models import classify_stall_stage
from cdp_ask.runner import HarvestRootMismatchError
from cdp_ask.unverifiable import is_host_lost_error

logger = logging.getLogger(__name__)

MAX_HOST_LOSS_RESUMES = 1

__all__ = ["MAX_HOST_LOSS_RESUMES", "finish_execution", "guard_execution"]


def _park_for_resume(registration_id: str) -> bool:
    """Park the dead-host row dormant so ``ensure_cse_attached`` relaunches it by URL.

    ``make_dormant`` refuses without a bound ``chat_url`` (nothing to resume)
    and leaves the row untouched; the caller then terminalizes as before.
    """
    row = cdp_registry._load_active().get(registration_id)
    if row is None:
        return False
    if row.get("status") == cdp_registry.STATUS_DORMANT:
        return True
    return cdp_registry.make_dormant(registration_id, reason="host_lost") is not None


async def _try_resume_after_host_loss(
    store: ExecutionStore, execution_id: str, error: str | None
) -> bool:
    """Route a host-loss failure to resume; False when the ordinary terminal applies."""
    record = await store.get(execution_id)
    if record is None or not record.registration_id:
        return False
    if record.abort_requested or record.resume_attempts >= MAX_HOST_LOSS_RESUMES:
        return False
    if not is_host_lost_error(error):
        return False
    parked = await asyncio.to_thread(_park_for_resume, record.registration_id)
    if not parked:
        return False
    record.resume_attempts += 1
    from cdp_ask.execution_resume import spawn_resume

    await spawn_resume(store, record, trigger="host_lost")
    return True


async def finish_execution(
    store: ExecutionStore, execution_id: str, payload: dict[str, Any]
) -> None:
    """Apply a run payload to the store — the single completion decision for an execution."""
    if payload.get("awaiting_wake_debt") and payload.get("ok"):
        await store.mark_awaiting_wake(execution_id, result=payload)
        return
    status = (
        "aborted"
        if payload.get("status") == "aborted"
        else ("completed" if payload.get("ok") else "failed")
    )
    error = payload.get("error")
    if status == "failed" and await _try_resume_after_host_loss(
        store, execution_id, error
    ):
        return
    stall = payload.get("stall_stage")
    if status == "failed" and not stall:
        stall = classify_stall_stage(error)
    await store.mark_terminal(
        execution_id,
        status=status,
        result=payload,
        error=error,
        stall_stage=stall if status == "failed" else None,
    )


async def _abort_requested(store: ExecutionStore, execution_id: str) -> bool:
    record = await store.get(execution_id)
    return bool(record and record.abort_requested)


async def guard_execution(
    store: ExecutionStore,
    execution_id: str,
    body: Callable[[], Awaitable[None]],
) -> None:
    """Run *body* as the execution's task; every escape becomes an explicit terminal."""
    try:
        await body()
    except asyncio.CancelledError:
        if store.shutting_down and not await _abort_requested(store, execution_id):
            # Process teardown, not an operator abort: the row keeps its in-flight
            # execution_state and its Chrome so the successor hydrates and resumes.
            logger.info(
                "execution %s parked in flight for successor (process draining)",
                execution_id,
            )
            raise
        with contextlib.suppress(Exception):
            await store.mark_terminal(
                execution_id,
                status="aborted",
                error="cancelled",
                stall_stage="mark_terminal",
            )
        raise
    except HarvestRootMismatchError as exc:
        logger.exception("execution %s harvest root mismatch", execution_id)
        await store.mark_terminal(
            execution_id,
            status="failed",
            error=str(exc),
            stall_stage="mark_terminal",
        )
    except Exception as exc:  # noqa: BLE001 — task boundary
        logger.exception("execution %s failed", execution_id)
        await store.mark_terminal(
            execution_id,
            status="failed",
            error=str(exc),
            stall_stage="mark_terminal",
        )
