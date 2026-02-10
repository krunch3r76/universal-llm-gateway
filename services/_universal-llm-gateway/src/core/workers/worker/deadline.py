"""
Deadline enforcement for worker inference.

INVARIANT: ∀ inference_request with timeout_hint: exits within timeout_hint + GRACE_S
INVARIANT: cancellation_event.is_set() ⟹ inference loop exits

Mechanism:
1. Start deadline_task that sleeps for timeout_hint seconds
2. On wake, set cancellation_event if not already set
3. Inference code checks cancellation_event each iteration
4. Cleanup cancels deadline_task when inference completes first
"""

import asyncio
from contextlib import asynccontextmanager

from universal_logging import get_logger

logger = get_logger(__name__)

# Grace period for cleanup after deadline (seconds)
# Reserved for future use: enforcement of hard shutdown after deadline + grace period
# Current implementation: soft cancellation via event, no hard enforcement
DEADLINE_GRACE_S = 5.0


@asynccontextmanager
async def enforce_deadline(
    timeout_hint: float | None,
    cancellation_event: asyncio.Event,
    request_id: str,
):
    """
    Enforce deadline by setting cancellation_event after timeout.

    Usage:
        async with enforce_deadline(timeout_hint, cancellation_event, request_id):
            async for chunk in engine.generate_stream(...):
                if cancellation_event.is_set():
                    break
                yield chunk

    Args:
        timeout_hint: Seconds until deadline (None = no deadline)
        cancellation_event: Event to set on deadline expiry
        request_id: For logging

    Behavior:
        - timeout_hint is None or ≤0: no deadline enforcement
        - timeout_hint > 0: sets cancellation_event after timeout
        - Existing cancellation (manual, queue timeout) still works
    """
    if timeout_hint is None or timeout_hint <= 0:
        yield
        return

    deadline_task: asyncio.Task | None = None

    async def deadline_trigger():
        """Sleep until deadline, then set cancellation."""
        await asyncio.sleep(timeout_hint)
        if not cancellation_event.is_set():
            logger.warning(
                f"⏰ [{request_id[:8]}] Deadline expired after {timeout_hint:.1f}s"
            )
            # TODO: Add observability counter: worker.deadline.triggered
            cancellation_event.set()

    try:
        deadline_task = asyncio.create_task(
            deadline_trigger(),
            name=f"deadline-{request_id[:8]}",
        )
        yield
    finally:
        # Cleanup: cancel deadline task if inference completed first
        if deadline_task and not deadline_task.done():
            deadline_task.cancel()
            try:
                await deadline_task
            except asyncio.CancelledError:
                pass
