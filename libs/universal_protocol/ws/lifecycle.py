"""Stream lifecycle management for WebSocket streaming.

Implements strict three-state model per MVP spec:
- READY: Stream initialized, ready for inference
- STREAMING: Frames being sent to consumer
- CLEANUP: Stream closed, all resources released

Design:
- No half-closed states, no reconnect logic
- Cleanup cancels all tasks immediately on stream end
- Any error (socket failure, timeout, etc) → immediate cleanup
"""

import asyncio
from enum import Enum

from universal_logging import get_logger

logger = get_logger(__name__)


class StreamState(Enum):
    """Stream lifecycle state machine.

    States:
    - READY: Initialized, waiting for first token
    - STREAMING: Active token streaming in progress
    - CLEANUP: Terminal state, all resources released
    """

    READY = "ready"
    STREAMING = "streaming"
    CLEANUP = "cleanup"

    def __repr__(self) -> str:
        """String representation."""
        return f"StreamState.{self.name}"


class StreamContext:
    """Manages stream lifecycle and cleanup.

    Enforces state machine transitions:
    - READY → STREAMING (on first token)
    - STREAMING → CLEANUP (on done or error)
    - READY → CLEANUP (on early termination)

    Cleanup semantics:
    - cleanup_nowait(): Cancel tasks, schedule background waiter (non-blocking)
    - cleanup_wait(): Cancel tasks and await completion (blocking)
    """

    def __init__(self, stream_id: str) -> None:
        """Initialize stream context.

        Inputs:
            stream_id: Unique stream identifier (e.g., "stream-abc123")
        """
        self.stream_id = stream_id
        self.state = StreamState.READY
        self._tasks: set[asyncio.Task[object]] = set()
        # Lock required: check-then-set pattern + await inside critical section
        self._cleanup_lock: asyncio.Lock | None = None
        self._cleanup_done = False

    def _ensure_lock(self) -> asyncio.Lock:
        """Ensure cleanup lock is initialized (lazy initialization)."""
        if self._cleanup_lock is None:
            self._cleanup_lock = asyncio.Lock()
        return self._cleanup_lock

    def add_task(self, task: asyncio.Task[object]) -> None:
        """Register a task for cleanup tracking.

        Inputs:
            task: Task to track for cancellation during cleanup

        Raises:
            RuntimeError: If cleanup has already started (prevents task leaks)
        """
        if self._cleanup_done:
            # Cancel immediately if cleanup already started
            task.cancel()
            logger.warning(
                f"Stream {self.stream_id}: task {task.get_name()} added after cleanup, "
                "cancelled immediately"
            )
            return

        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def transition_to_streaming(self) -> None:
        """Transition from READY to STREAMING.

        Raises:
            RuntimeError: If already in STREAMING or CLEANUP state
        """
        if self.state != StreamState.READY:
            raise RuntimeError(
                f"Cannot transition to STREAMING from {self.state}; "
                "stream already active or cleaned up"
            )
        self.state = StreamState.STREAMING
        logger.debug(f"Stream {self.stream_id} transitioned to STREAMING")

    def cleanup_nowait(self) -> None:
        """Cancel all tasks and schedule background cleanup (non-blocking).

        Use this in request paths (e.g., WebSocket handler finally block).

        Idempotent: safe to call multiple times.

        Side-effects:
            - Cancels all tracked tasks immediately
            - Schedules background task to await their completion
            - Sets state to CLEANUP
        """
        if self._cleanup_done:
            return

        self._cleanup_done = True
        old_state = self.state
        self.state = StreamState.CLEANUP

        # Cancel all tracked tasks immediately (sync, non-blocking)
        tasks_to_cancel = list(self._tasks)
        for task in tasks_to_cancel:
            if not task.done():
                task.cancel()
                logger.debug(
                    f"Stream {self.stream_id} cancelled task {task.get_name()}"
                )

        # Schedule background waiter if there are tasks to await
        if tasks_to_cancel:

            async def _background_cleanup():
                try:
                    await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
                except Exception as e:
                    logger.warning(
                        f"Background cleanup error for {self.stream_id}: {e}"
                    )

            waiter = asyncio.create_task(
                _background_cleanup(), name=f"cleanup-{self.stream_id}"
            )
            # Fire-and-forget: don't track this task
            waiter.add_done_callback(lambda t: None)

        logger.info(
            f"Stream {self.stream_id} cleanup scheduled "
            f"(was {old_state}, tasks={len(tasks_to_cancel)})"
        )

    async def cleanup_wait(self) -> None:
        """Cleanup stream and await all task completions (blocking).

        Use this when you need to ensure all tasks are complete before proceeding.
        NOT for use in request paths.

        Idempotent: safe to call multiple times.

        Lock justification: Protects check-then-set of _cleanup_done flag
        and ensures only one cleanup runs. Has await (asyncio.gather) inside.
        """
        lock = self._ensure_lock()
        async with lock:
            if self._cleanup_done:
                return

            self._cleanup_done = True
            old_state = self.state
            self.state = StreamState.CLEANUP

            # Cancel all tracked tasks
            for task in list(self._tasks):
                if not task.done():
                    task.cancel()
                    logger.debug(
                        f"Stream {self.stream_id} cancelled task {task.get_name()}"
                    )

            # Wait for all tasks to finish
            if self._tasks:
                await asyncio.gather(
                    *self._tasks,
                    return_exceptions=True,
                )

            logger.info(
                f"Stream {self.stream_id} cleaned up "
                f"(was {old_state}, tasks={len(self._tasks)})"
            )

    def is_ready(self) -> bool:
        """Check if stream is in READY state."""
        return self.state == StreamState.READY

    def is_streaming(self) -> bool:
        """Check if stream is in STREAMING state."""
        return self.state == StreamState.STREAMING

    def is_cleanup(self) -> bool:
        """Check if stream is in CLEANUP state."""
        return self.state == StreamState.CLEANUP

    def __repr__(self) -> str:
        return f"StreamContext(id={self.stream_id}, state={self.state})"
