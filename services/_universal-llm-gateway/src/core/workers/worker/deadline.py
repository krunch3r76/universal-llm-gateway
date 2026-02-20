"""
Timeout enforcement for worker inference.

Two modes:
- enforce_deadline: absolute timeout (non-streaming requests)
- enforce_idle_timeout: per-chunk idle timeout (streaming requests)

INVARIANT: cancellation_event.is_set() ⟹ inference loop exits
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from universal_logging import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def enforce_deadline(
    timeout_hint: float | None,
    cancellation_event: asyncio.Event,
    request_id: str,
) -> AsyncIterator[None]:
    """
    Enforce absolute deadline for non-streaming requests.

    Only activates when timeout_hint is explicitly provided (not None).
    Sets cancellation_event after timeout_hint seconds.

    Args:
        timeout_hint: Seconds until deadline (None = no enforcement)
        cancellation_event: Event to set on deadline expiry
        request_id: For logging
    """
    if timeout_hint is None or timeout_hint <= 0:
        yield
        return

    deadline_task: asyncio.Task[None] | None = None

    async def deadline_trigger() -> None:
        await asyncio.sleep(timeout_hint)
        if not cancellation_event.is_set():
            logger.warning(
                "⏰ [%s] Deadline expired after %.1fs",
                request_id[:8],
                timeout_hint,
            )
            cancellation_event.set()

    try:
        deadline_task = asyncio.create_task(
            deadline_trigger(),
            name=f"deadline-{request_id[:8]}",
        )
        yield
    finally:
        if deadline_task and not deadline_task.done():
            deadline_task.cancel()
            try:
                await deadline_task
            except asyncio.CancelledError:
                pass


@asynccontextmanager
async def enforce_idle_timeout(
    idle_seconds: float | None,
    cancellation_event: asyncio.Event,
    request_id: str,
) -> AsyncIterator[Callable[[], None]]:
    """
    Enforce per-chunk idle timeout for streaming requests.

    Yields a reset() callable. The caller MUST invoke reset() on each
    chunk to restart the idle timer. If no reset() call arrives within
    idle_seconds, cancellation_event is set.

    Only activates when idle_seconds is explicitly provided (not None).

    Args:
        idle_seconds: Max seconds between chunks (None = no enforcement)
        cancellation_event: Event to set on idle expiry
        request_id: For logging

    Yields:
        reset: Callable that resets the idle timer (call on each chunk)
    """
    if idle_seconds is None or idle_seconds <= 0:
        yield lambda: None
        return

    activity = asyncio.Event()

    def reset() -> None:
        activity.set()

    watchdog_task: asyncio.Task[None] | None = None

    async def idle_watchdog() -> None:
        while not cancellation_event.is_set():
            activity.clear()
            try:
                await asyncio.wait_for(activity.wait(), timeout=idle_seconds)
            except TimeoutError:
                if not cancellation_event.is_set():
                    logger.warning(
                        "⏰ [%s] Idle timeout: no chunk for %.1fs",
                        request_id[:8],
                        idle_seconds,
                    )
                    cancellation_event.set()
                return

    try:
        watchdog_task = asyncio.create_task(
            idle_watchdog(),
            name=f"idle-{request_id[:8]}",
        )
        yield reset
    finally:
        if watchdog_task and not watchdog_task.done():
            watchdog_task.cancel()
            try:
                await watchdog_task
            except asyncio.CancelledError:
                pass
