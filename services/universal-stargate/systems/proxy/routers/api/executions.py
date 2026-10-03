"""Execution read, stats, and cancel.

``GET /executions/stats`` is registered before ``GET /executions/{execution_id}``
so the literal path is not captured as an id.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from systems.pipeline.core.execution.dispatch_journal import fetch_terminal

from ...dependencies import get_auth_dependency, get_proxy
from ...stargate_core import StargateProxy
from .dispatch_bus_recovery import recover_execution_from_bus_thread
from .pipelines_dispatch import _error_response, _get_tracker

router = APIRouter(tags=["executions"])
_MAX_WAIT_SECONDS = 60.0


@router.get("/executions/stats")
async def get_dispatch_stats(
    proxy: StargateProxy = Depends(get_proxy),
    _current_user: dict[str, object] = Depends(get_auth_dependency),
) -> JSONResponse:
    """Return tracker occupancy snapshot."""
    tracker = _get_tracker(proxy)
    if tracker is None:
        return _error_response(
            503,
            "pipeline_dispatch_unavailable",
            "Async pipeline dispatch tracker is not initialized.",
        )

    now = time.monotonic()
    running = 0
    completed = 0
    failed = 0
    oldest_terminal: float | None = None
    oldest_running: float | None = None
    for record in tracker.records.values():
        if record.status == "running":
            running += 1
            age = now - record.started_at_monotonic
            if oldest_running is None or age > oldest_running:
                oldest_running = age
        elif record.status == "completed":
            completed += 1
            if record.completed_at_monotonic is not None:
                age = now - record.completed_at_monotonic
                if oldest_terminal is None or age > oldest_terminal:
                    oldest_terminal = age
        elif record.status == "failed":
            failed += 1
            if record.completed_at_monotonic is not None:
                age = now - record.completed_at_monotonic
                if oldest_terminal is None or age > oldest_terminal:
                    oldest_terminal = age

    return JSONResponse(
        status_code=200,
        content={
            "running": running,
            "completed": completed,
            "failed": failed,
            "terminal": completed + failed,
            "max_records": tracker.max_records,
            "retention_seconds": tracker.retention_seconds,
            "oldest_terminal_age_seconds": oldest_terminal,
            "oldest_running_age_seconds": oldest_running,
        },
    )


@router.get("/executions/{execution_id}")
async def get_pipeline_execution(
    execution_id: str,
    wait: float = Query(0.0, ge=0.0),
    proxy: StargateProxy = Depends(get_proxy),
    _current_user: dict[str, object] = Depends(get_auth_dependency),
) -> JSONResponse:
    """Fetch tracker state for an async-dispatched pipeline execution."""
    tracker = _get_tracker(proxy)
    if tracker is None:
        return _error_response(
            503,
            "pipeline_dispatch_unavailable",
            "Async pipeline dispatch tracker is not initialized.",
        )

    wait_clamped = min(max(0.0, wait), _MAX_WAIT_SECONDS)
    record = await tracker.wait_for_terminal(execution_id, wait_clamped)
    if record is None:
        journal_record = await fetch_terminal(
            execution_id,
            event_bus=getattr(proxy, "event_bus", None),
        )
        if journal_record is not None:
            return JSONResponse(status_code=200, content=journal_record)
        recovered = await recover_execution_from_bus_thread(
            execution_id,
            url=tracker._agent_bus_url,
            auth_token=tracker._agent_bus_token,
            wait_seconds=wait_clamped,
        )
        if recovered is not None:
            return JSONResponse(status_code=200, content=recovered)
        return _error_response(
            404,
            "execution_id_expired_or_unknown",
            f"Unknown or expired execution_id '{execution_id}'.",
        )

    return JSONResponse(status_code=200, content=record.to_dict())


@router.delete("/executions/{execution_id}")
async def cancel_pipeline_execution(
    request: Request,
    execution_id: str,
    proxy: StargateProxy = Depends(get_proxy),
    _current_user: dict[str, object] = Depends(get_auth_dependency),
) -> JSONResponse:
    """Cancel an in-flight async-dispatched pipeline execution."""
    tracker = _get_tracker(proxy)
    if tracker is None:
        return _error_response(
            503,
            "pipeline_dispatch_unavailable",
            "Async pipeline dispatch tracker is not initialized.",
        )

    record = tracker.get(execution_id)
    if record is None:
        return _error_response(
            404,
            "execution_id_expired_or_unknown",
            f"Unknown or expired execution_id '{execution_id}'.",
        )

    if record.status in {"completed", "failed"}:
        return JSONResponse(status_code=200, content=record.to_dict())

    task_index: dict[str, asyncio.Task[Any]] = getattr(
        request.app.state, "pipeline_task_index", {}
    )
    task = task_index.get(execution_id)
    if task is None or task.done():
        tracker.fail_execution(
            execution_id,
            code="pipeline_execution_cancelled",
            message="Cancel requested; no live task found.",
        )
    else:
        task.cancel()

    event_bus = getattr(proxy, "event_bus", None)
    if event_bus is not None:
        from systems.pipeline.core.events.dispatch import PipelineDispatchCancelled

        asyncio.create_task(
            event_bus.publish_nowait(
                PipelineDispatchCancelled(
                    pipeline_id=record.pipeline,
                    execution_id=execution_id,
                    source="operator",
                )
            )
        )

    terminal_record = await tracker.wait_for_terminal(execution_id, timeout_seconds=5.0)
    payload = (
        terminal_record.to_dict()
        if terminal_record is not None
        else {"execution_id": execution_id, "status": "unknown"}
    )
    return JSONResponse(status_code=200, content=payload)
