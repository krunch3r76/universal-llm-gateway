"""Operator cancel orchestration — thin route helper over ledger authority."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import Request
from universal_protocol import error_envelope

from services.git_integration_worker.admission import WorkAdmissionController
from services.git_integration_worker.config import load_config
from services.git_integration_worker.cursor_dispatch_ledger import (
    CancelDispatchResult,
    CursorDispatchLedger,
    DispatchNotFound,
    NotCancellableRunning,
)
from services.git_integration_worker.cursor_sdk_cancel_events import (
    emit_sdk_worker_cancelled,
)
from services.git_integration_worker.cursor_sdk_events import emit_write_lease_released
from services.git_integration_worker.cursor_sdk_worktree_prune import (
    maybe_prune_worktree_on_terminal,
)


async def operator_cancel_dispatch(
    *,
    dispatch_id: str,
    cancel_reason: str | None,
    cancelled_by: str | None,
    controller: WorkAdmissionController,
    request: Request | None,
) -> tuple[int, dict[str, Any]]:
    """Cancel a queued/admitted row; emit events; promote FIFO when lease held."""
    ledger = CursorDispatchLedger.instance()
    try:
        result = await asyncio.to_thread(
            ledger.cancel_dispatch,
            dispatch_id=dispatch_id,
            cancel_reason=cancel_reason,
            cancelled_by=cancelled_by,
        )
    except DispatchNotFound:
        return 404, error_envelope(
            code="not_found",
            message=f"dispatch_id {dispatch_id!r} not found",
            source="git_integration_worker",
            retryable=False,
            data={"dispatch_id": dispatch_id},
        )
    except NotCancellableRunning as exc:
        return 409, error_envelope(
            code="not_cancellable_running",
            message=str(exc),
            source="git_integration_worker",
            retryable=False,
            data={
                "dispatch_id": exc.dispatch_id,
                "status": exc.status,
                "thread_id": exc.thread_id,
            },
        )
    if result.outcome == "already_terminal":
        return 200, _cancel_response_body(result)
    await _emit_cancel_side_effects(
        result=result,
        dispatch_id=dispatch_id,
        cancel_reason=cancel_reason,
        controller=controller,
        request=request,
    )
    return 200, _cancel_response_body(result)


def _cancel_response_body(result: CancelDispatchResult) -> dict[str, Any]:
    body = dict(result.row)
    body["outcome"] = result.outcome
    return body


async def _emit_cancel_side_effects(
    *,
    result: CancelDispatchResult,
    dispatch_id: str,
    cancel_reason: str | None,
    controller: WorkAdmissionController,
    request: Request | None,
) -> None:
    from services.git_integration_worker.routes.cursor_sdk import (
        _config,
        _promote_queued_for_lease,
    )

    row = result.row
    thread_id = row.get("thread_id")
    emit_sdk_worker_cancelled(
        dispatch_id=dispatch_id,
        method="operator_cancel",
        reason=cancel_reason or "operator_cancel",
        thread_id=str(thread_id) if thread_id else None,
        terminal_status="cancelled",
    )
    cfg = _config(request) if request is not None else load_config()
    await asyncio.to_thread(
        maybe_prune_worktree_on_terminal,
        dispatch_id=dispatch_id,
        source_repo=cfg.source_repo,
    )
    if result.lease_key:
        emit_write_lease_released(
            dispatch_id=dispatch_id,
            source_repo=result.lease_key,
        )
        if result.needs_promote and not controller.is_draining():
            await _promote_queued_for_lease(
                lease_key=result.lease_key,
                controller=controller,
                request=request,
            )
