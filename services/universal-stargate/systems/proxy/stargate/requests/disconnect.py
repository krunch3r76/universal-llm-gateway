"""
Client disconnection guard for request execution.

Races a coroutine against a disconnect monitor task.  If the client
disconnects while the coroutine is suspended (e.g. waiting in the
capacity pool FIFO queue), the coroutine is cancelled so the slot is
returned to the pool without executing an unnecessary forward.

INVARIANT: ∀ disconnect-cancelled tasks: capacity token released via
    BaseException handler in RequestExecutor._execute_normal_mode before
    CancelledError propagates to the caller.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

if TYPE_CHECKING:
    from fastapi import Request

logger = get_logger(__name__)

_DISCONNECT_POLL_INTERVAL_S = 1.0
_DISCONNECT_GRACE_S = 0.5


async def execute_with_disconnect_guard(
    coro: Any,
    request: Request,
    request_id: str = "",
) -> Any:
    """Execute *coro* and cancel it if the client disconnects.

    Races the coroutine against a polling disconnect monitor.  When the
    monitor detects disconnection the execution task is cancelled —
    propagating CancelledError through the capacity pool, which removes
    the waiter from the FIFO queue and returns any held slot.

    Use when the coroutine may block waiting for a capacity slot:
        response = await execute_with_disconnect_guard(
            executor.execute_request(context), request, request_id
        )

    ∀ normal completion: monitor cancelled, coroutine result returned.
    ∀ disconnect: coroutine cancelled, CancelledError raised.
    ∀ outer CancelledError: both tasks cancelled, CancelledError re-raised.
    """
    exec_task = asyncio.create_task(coro, name=f"exec-{request_id[:8]}")

    async def _monitor() -> None:
        await asyncio.sleep(_DISCONNECT_GRACE_S)
        while True:
            try:
                if await request.is_disconnected():
                    logger.info(
                        "🔌 Client disconnected while queued — cancelling request %s",
                        request_id[:8] or "(unknown)",
                    )
                    return
            except Exception as exc:
                logger.debug("Disconnect check error for %s: %s", request_id[:8], exc)
            await asyncio.sleep(_DISCONNECT_POLL_INTERVAL_S)

    monitor_task = asyncio.create_task(_monitor(), name=f"dc-monitor-{request_id[:8]}")

    try:
        done, _ = await asyncio.wait(
            [exec_task, monitor_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        if exec_task in done:
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass
            return exec_task.result()

        # Monitor returned → disconnect
        exec_task.cancel()
        try:
            await exec_task
        except (asyncio.CancelledError, Exception):
            pass
        raise asyncio.CancelledError("Client disconnected")

    except asyncio.CancelledError:
        for task in (exec_task, monitor_task):
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        raise
