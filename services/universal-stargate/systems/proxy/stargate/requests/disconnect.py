"""
Client disconnection guard for request execution.

Races a coroutine against a disconnect watcher.  If the client disconnects
while the coroutine is suspended (e.g. waiting in the capacity pool FIFO
queue), the coroutine is cancelled so the slot is returned to the pool
without executing an unnecessary forward.

The watcher is the shared ``wait_for_client_disconnect`` (one await on the
ASGI receive channel) rather than a ``Request.is_disconnected()`` poll loop,
which could survive its own cancellation and hold the guard open until the
response completed — see ``src.core.streaming.client_disconnect``.

INVARIANT: ∀ disconnect-cancelled tasks: capacity token released via
    BaseException handler in RequestExecutor._execute_normal_mode before
    CancelledError propagates to the caller.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

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

logger = get_logger(__name__)


async def execute_with_disconnect_guard(
    coro: Any,
    request: Request,
    request_id: str = "",
) -> Any:
    """Execute *coro* and cancel it if the client disconnects.

    Races the coroutine against the disconnect watcher.  When the watcher
    resolves the execution task is cancelled — propagating CancelledError
    through the capacity pool, which removes the waiter from the FIFO queue
    and returns any held slot.

    Use when the coroutine may block waiting for a capacity slot:
        response = await execute_with_disconnect_guard(
            executor.execute_request(context), request, request_id
        )

    ∀ normal completion: watcher stopped (bounded), coroutine result returned.
    ∀ disconnect: coroutine cancelled (bounded grace), CancelledError raised.
    ∀ outer CancelledError: both tasks cancelled (bounded), CancelledError re-raised.
    """
    short_id = request_id[:8] or "(unknown)"
    exec_task = asyncio.create_task(coro, name=f"exec-{short_id}")
    monitor_task = asyncio.create_task(
        wait_for_client_disconnect(request), name=f"dc-monitor-{short_id}"
    )

    try:
        done, _pending = await asyncio.wait(
            {exec_task, monitor_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        if exec_task in done:
            await stop_task(
                monitor_task,
                grace_s=WATCHER_STOP_GRACE_S,
                what=f"dc-monitor for request {short_id}",
            )
            return exec_task.result()

        logger.info(
            "🔌 Client disconnected while queued — cancelling request %s", short_id
        )
        await stop_task(
            exec_task,
            grace_s=WORK_STOP_GRACE_S,
            what=f"exec task for request {short_id}",
        )
        raise asyncio.CancelledError("Client disconnected")

    except asyncio.CancelledError:
        await stop_tasks(
            (exec_task, monitor_task),
            grace_s=WORK_STOP_GRACE_S,
            what=f"request {short_id}",
        )
        raise
