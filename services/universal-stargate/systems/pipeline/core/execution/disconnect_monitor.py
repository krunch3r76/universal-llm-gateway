"""Client-disconnect race for synchronous pipeline execution.

Called by ``executor/execution_loop.py`` for ``/v1/chat/completions`` pipeline
runs, which hold a live connection for the whole execution: when the client
goes away mid-DAG the executor is cancelled so model gates and slots return
immediately instead of finishing work nobody will read.

The watcher is ``src.core.streaming.client_disconnect.wait_for_client_disconnect``
— one ``await`` on the ASGI receive channel, no polling. The previous
``Request.is_disconnected()`` poll loop could survive its own cancellation (see
that module's docstring); the hang then surfaced as ``pipeline.completed`` at
exactly ``timeout_seconds`` with the DAG long finished.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from universal_logging import get_logger

from src.core.streaming.client_disconnect import (
    WATCHER_STOP_GRACE_S,
    WORK_STOP_GRACE_S,
    stop_task,
    stop_tasks,
    wait_for_client_disconnect,
)

if TYPE_CHECKING:
    from fastapi import Request

    from .executor import DAGExecutor

logger = get_logger(__name__)


async def execute_with_disconnect_monitoring(
    dag_executor: DAGExecutor,
    http_request: Request,
    pipeline_id: str,
    execution_id: str,
) -> None:
    """Run ``dag_executor.execute()`` and cancel it if the client disconnects first.

    Races the DAG task against the disconnect watcher. On normal completion the
    watcher is stopped with a bounded wait and any DAG exception is re-raised.
    On disconnect the DAG task is cancelled, given ``WORK_STOP_GRACE_S`` to
    release gates, and ``CancelledError("Client disconnected")`` is raised so
    the caller emits ``PipelineCancelled``. A cancellation aimed at this
    coroutine (e.g. the caller's ``asyncio.wait_for`` deadline) unwinds both
    tasks and propagates — it is never converted into a normal return.
    """
    execution_task = asyncio.create_task(dag_executor.execute(), name="dag-execution")
    monitor_task = asyncio.create_task(
        wait_for_client_disconnect(http_request), name="disconnect-monitor"
    )

    try:
        done, _pending = await asyncio.wait(
            {execution_task, monitor_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        if execution_task in done:
            await stop_task(
                monitor_task,
                grace_s=WATCHER_STOP_GRACE_S,
                what=f"disconnect-monitor for '{pipeline_id}' ({execution_id})",
            )
            execution_task.result()
            return

        logger.info(
            "🔌 Client disconnected during pipeline '%s' execution (execution_id=%s)",
            pipeline_id,
            execution_id,
        )
        await stop_task(
            execution_task,
            grace_s=WORK_STOP_GRACE_S,
            what=f"dag-execution for pipeline '{pipeline_id}' ({execution_id})",
        )
        raise asyncio.CancelledError("Client disconnected")

    except asyncio.CancelledError:
        await stop_tasks(
            (execution_task, monitor_task),
            grace_s=WORK_STOP_GRACE_S,
            what=f"pipeline '{pipeline_id}' ({execution_id})",
        )
        raise
