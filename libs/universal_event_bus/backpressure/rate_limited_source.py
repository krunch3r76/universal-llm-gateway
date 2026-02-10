"""
Per-source rate limiting with bounded queue.

Used for telemetry backpressure in federation.

INVARIANT:
  ∀ source s: event_rate(s) ≤ max_events_per_second
  ∧ queue_overflow(s) ⟹ drop_policy_applied
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


class OverflowPolicy(StrEnum):
    """
    Queue overflow policies for rate limiting.

    Note: This enum is also defined in systems/federation/config.py.
    Both use identical string values (StrEnum) for cross-module comparison.
    Kept separate to avoid circular imports between libs/ and services/.
    """

    DROP_OLDEST = "drop_oldest"
    DROP_NEWEST = "drop_newest"
    BACKPRESSURE = "backpressure"


@dataclass(slots=True)
class RateLimitConfig:
    """Rate limiting configuration."""

    max_queue_size: int = 100
    max_events_per_second: float = 50.0
    overflow_policy: OverflowPolicy = OverflowPolicy.DROP_OLDEST


class RateLimitedEventSource:
    """
    Per-source rate limiting with bounded queue.

    INVARIANT:
      - Events enqueued up to max_queue_size
      - Overflow applies drop_policy
      - Consumption rate ≤ max_events_per_second
    """

    def __init__(
        self,
        source_id: str,
        config: RateLimitConfig,
        on_event: Callable[[str, dict[str, Any]], Awaitable[None]],
        on_drop: Callable[[str, str], Awaitable[None]] | None = None,
    ):
        """
        Args:
            source_id: Identifier for this source
            config: Rate limiting configuration
            on_event: Callback(signal, payload) for events
            on_drop: Callback(source_id, reason) when events dropped
        """
        self._source_id = source_id
        self._config = config
        self._on_event = on_event
        self._on_drop = on_drop

        self._queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue(
            maxsize=config.max_queue_size
        )
        self._consumer_task: asyncio.Task | None = None
        self._running = False

        # Rate limiting state
        self._last_consume_time = time.time()
        self._min_interval = 1.0 / config.max_events_per_second

    @property
    def queue_depth(self) -> int:
        """Current queue depth."""
        return self._queue.qsize()

    def start(self) -> None:
        """Start the consumer task."""
        if self._running:
            return

        self._running = True
        self._consumer_task = asyncio.create_task(
            self._consumer_loop(),
            name=f"rate-limited-{self._source_id}",
        )

    async def stop(self) -> None:
        """Stop the consumer task."""
        self._running = False

        if self._consumer_task:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass
            self._consumer_task = None

    def clear_queue(self) -> int:
        """
        Clear all queued events.

        Used when destination becomes unavailable (e.g., Master disconnects).
        Prevents unbounded queue growth during disconnect periods.

        Returns:
            Number of cleared events
        """
        cleared = 0
        while True:
            try:
                self._queue.get_nowait()
                cleared += 1
            except asyncio.QueueEmpty:
                break
        return cleared

    async def enqueue(self, signal: str, payload: dict[str, Any]) -> bool:
        """
        Enqueue event for processing.

        Returns True if enqueued, False if dropped.
        """
        if not self._running:
            return False

        try:
            self._queue.put_nowait((signal, payload))
            return True
        except asyncio.QueueFull:
            # Apply overflow policy
            if self._config.overflow_policy == OverflowPolicy.DROP_NEWEST:
                # Drop this event
                if self._on_drop:
                    await self._on_drop(self._source_id, "queue_full_drop_newest")
                return False
            elif self._config.overflow_policy == OverflowPolicy.DROP_OLDEST:
                # Drop oldest event
                try:
                    self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass

                if self._on_drop:
                    await self._on_drop(self._source_id, "queue_full_drop_oldest")

                # Try again
                try:
                    self._queue.put_nowait((signal, payload))
                    return True
                except asyncio.QueueFull:
                    return False
            else:
                # OverflowPolicy.BACKPRESSURE - block
                await self._queue.put((signal, payload))
                return True

    async def _consumer_loop(self) -> None:
        """Consumer loop with rate limiting."""
        while self._running:
            try:
                # Wait for event
                signal, payload = await asyncio.wait_for(
                    self._queue.get(),
                    timeout=1.0,
                )

                # Rate limit
                now = time.time()
                elapsed = now - self._last_consume_time
                if elapsed < self._min_interval:
                    await asyncio.sleep(self._min_interval - elapsed)

                self._last_consume_time = time.time()

                # Process event
                try:
                    await self._on_event(signal, payload)
                except Exception as e:
                    logger.error(f"Event handler error: {e}")

            except TimeoutError:
                continue
            except asyncio.CancelledError:
                break
