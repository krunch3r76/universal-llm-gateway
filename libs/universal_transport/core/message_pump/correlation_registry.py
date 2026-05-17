"""
Correlation registry for the universal transport message pump.

Encapsulates per-correlation queues, expiration tasks, and metadata that
formerly lived in MessagePump. Supplies O(1) routing (route_message) and
dedicated get_message accessors. Keeps TransportError out of this module.
"""

import asyncio
import time
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)

# Default correlation timeout (5 minutes)
CORRELATION_TIMEOUT = 300.0


class CorrelationRegistry:
    """
    Owns correlation-specific queues, expiration tasks and activity metadata.

    Event-driven expiration: each correlation owns an independent asyncio Task
    that sleeps for its timeout and self-unregisters on inactivity. Activity
    (via route_message) refreshes the timer by rescheduling the task.
    """

    def __init__(self) -> None:
        self._correlation_queues: dict[str, asyncio.Queue] = {}
        self._correlation_metadata: dict[str, dict[str, Any]] = {}
        self._correlation_expiration_tasks: dict[str, asyncio.Task] = {}
        logger.debug("CorrelationRegistry initialized (per-correlation expiration)")

    @property
    def active_count(self) -> int:
        """Count of registered correlations with dedicated queues."""
        return len(self._correlation_queues)

    def is_registered(self, correlation_id: str) -> bool:
        """True when the correlation has an active dedicated queue."""
        return correlation_id in self._correlation_queues

    def register(
        self, correlation_id: str, timeout: float = CORRELATION_TIMEOUT
    ) -> None:
        """
        Register correlation for direct O(1) routing.

        Creates dedicated queue + metadata + expiration task. If already
        present, refreshes the expiration timer instead.
        """
        if self.is_registered(correlation_id):
            self._refresh_correlation_expiration(correlation_id, timeout)
            return

        now = time.time()
        self._correlation_queues[correlation_id] = asyncio.Queue()
        self._correlation_metadata[correlation_id] = {
            "created_at": now,
            "last_activity": now,
            "message_count": 0,
            "timeout": timeout,
        }
        self._schedule_correlation_expiration(correlation_id, timeout)
        logger.debug(f"Registered correlation queue: {correlation_id}")

    def _schedule_correlation_expiration(
        self, correlation_id: str, timeout: float
    ) -> None:
        """(Re)schedule the per-correlation expiration task."""
        if correlation_id in self._correlation_expiration_tasks:
            task = self._correlation_expiration_tasks[correlation_id]
            if not task.done():
                task.cancel()

        task = asyncio.create_task(
            self._expire_correlation(correlation_id, timeout),
            name=f"correlation-expire-{correlation_id[:8]}",
        )
        self._correlation_expiration_tasks[correlation_id] = task

    async def _expire_correlation(self, correlation_id: str, timeout: float) -> None:
        """Sleep then unregister if no activity since last schedule."""
        try:
            await asyncio.sleep(timeout)
            if correlation_id in self._correlation_metadata:
                metadata = self._correlation_metadata[correlation_id]
                elapsed = time.time() - metadata["last_activity"]
                if elapsed >= timeout:
                    self.unregister(correlation_id)
                    logger.warning(
                        f"Correlation {correlation_id[:8]} expired after "
                        f"{elapsed:.1f}s inactivity"
                    )
        except asyncio.CancelledError:
            pass

    def _refresh_correlation_expiration(
        self, correlation_id: str, timeout: float | None = None
    ) -> None:
        """Update last_activity and reschedule expiration task."""
        if correlation_id not in self._correlation_metadata:
            return
        self._correlation_metadata[correlation_id]["last_activity"] = time.time()
        if timeout is None:
            timeout = self._correlation_metadata[correlation_id].get(
                "timeout", CORRELATION_TIMEOUT
            )
        self._schedule_correlation_expiration(correlation_id, timeout)

    def unregister(self, correlation_id: str) -> None:
        """
        Cancel expiration task, drain queue, drop metadata for one correlation.
        """
        if correlation_id in self._correlation_expiration_tasks:
            task = self._correlation_expiration_tasks.pop(correlation_id)
            if not task.done():
                task.cancel()

        if correlation_id in self._correlation_queues:
            queue = self._correlation_queues.pop(correlation_id)
            while not queue.empty():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            self._correlation_metadata.pop(correlation_id, None)
            logger.debug(f"Unregistered correlation queue: {correlation_id}")

    def clear(self) -> None:
        """Cancel all expiration tasks, drain all queues, reset registry state."""
        for task in self._correlation_expiration_tasks.values():
            if not task.done():
                task.cancel()
        self._correlation_expiration_tasks.clear()

        for queue in self._correlation_queues.values():
            while not queue.empty():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
        self._correlation_queues.clear()
        self._correlation_metadata.clear()

    async def get_message(
        self, correlation_id: str, timeout: float | None = None
    ) -> dict[str, Any] | None:
        """
        Wait for next message on the correlation's dedicated queue.

        Raises KeyError when correlation is not registered (pump translates).
        """
        if not self.is_registered(correlation_id):
            raise KeyError(
                f"Correlation ID {correlation_id} not registered. "
                f"Call register_correlation() first."
            )

        queue = self._correlation_queues[correlation_id]
        try:
            if timeout is not None:
                return await asyncio.wait_for(queue.get(), timeout=timeout)
            return await queue.get()
        except TimeoutError:
            return None

    async def route_message(self, correlation_id: str, message: dict[str, Any]) -> bool:
        """
        O(1) put into correlation queue + refresh activity + reschedule expiration.

        Returns False if the correlation was not registered.
        """
        if not self.is_registered(correlation_id):
            return False

        await self._correlation_queues[correlation_id].put(message)

        if correlation_id in self._correlation_metadata:
            metadata = self._correlation_metadata[correlation_id]
            metadata["last_activity"] = time.time()
            metadata["message_count"] += 1
            self._refresh_correlation_expiration(correlation_id)

        return True

    def get_stats(self) -> dict[str, Any]:
        """Return correlation statistics snapshot (active count, ages, queue sizes)."""
        if not self._correlation_metadata:
            return {
                "active_correlations": 0,
                "oldest_correlation_age": 0,
                "total_messages_in_queues": 0,
                "correlation_details": [],
            }

        now = time.time()
        oldest_age = 0
        total_messages = 0
        correlation_details = []

        for corr_id, metadata in self._correlation_metadata.items():
            age = now - metadata["created_at"]
            inactive_time = now - metadata["last_activity"]
            if age > oldest_age:
                oldest_age = age
            queue = self._correlation_queues.get(corr_id)
            queue_size = queue.qsize() if queue else 0
            total_messages += queue_size
            correlation_details.append(
                {
                    "correlation_id": corr_id,
                    "age_seconds": age,
                    "inactive_seconds": inactive_time,
                    "message_count": metadata["message_count"],
                    "queue_size": queue_size,
                }
            )

        return {
            "active_correlations": len(self._correlation_queues),
            "oldest_correlation_age": oldest_age,
            "total_messages_in_queues": total_messages,
            "correlation_details": correlation_details,
        }
