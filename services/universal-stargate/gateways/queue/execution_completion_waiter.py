"""
Event-driven capacity waiter.

Wakes on capacity hints (MODEL_EXECUTION_COMPLETED or MODEL_CAPACITY_FREED).
Invariant: ∀ capacity events are captured (no missed signals)
Pattern: Epoch counter + asyncio.Condition (not Event.clear())
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


class CapacityWaiter:
    """
    Wait for capacity events without race conditions.

    Subscribes to both MODEL_EXECUTION_COMPLETED and MODEL_CAPACITY_FREED.
    Uses epoch counter to ensure no signals are missed between
    checking and waiting.

    Invariant: ∀ capacity_event ⟹ epoch incremented ⟹ waiters notified
    """

    def __init__(self, event_bus: Any = None) -> None:
        """
        Initialize waiter.

        Args:
            event_bus: Event bus for capacity events
        """
        self._event_bus = event_bus
        self._condition = asyncio.Condition()
        self._epoch = 0  # Monotonically increasing on each capacity event
        self._subscribed = False
        self._loop: asyncio.AbstractEventLoop | None = None

    def _subscribe_to_events(self, loop: asyncio.AbstractEventLoop) -> None:
        """
        Subscribe to capacity events (lazy, loop rebinding).

        Subscribes to both:
        - MODEL_EXECUTION_COMPLETED: Request finished, gateway has capacity
        - MODEL_CAPACITY_FREED: Gateway reported idle/unloaded, capacity available
        """
        # Update loop reference if changed (handles lifecycle with different loops)
        if self._loop is not loop:
            self._loop = loop
            logger.debug(f"CapacityWaiter loop rebound (id={id(loop)})")

        if self._subscribed or not self._event_bus:
            return

        from src.scheduling.events import (
            MODEL_CAPACITY_FREED,
            MODEL_EXECUTION_COMPLETED,
        )

        def on_capacity_event(_event) -> None:
            """Increment epoch and notify all waiters (thread-safe)."""
            if self._loop is None or self._loop.is_closed():
                return
            # Use call_soon_threadsafe for safety when called from any context
            self._loop.call_soon_threadsafe(self._schedule_notify)

        # Subscribe to both signals
        self._event_bus.subscribe_async(MODEL_EXECUTION_COMPLETED, on_capacity_event)
        self._event_bus.subscribe_async(MODEL_CAPACITY_FREED, on_capacity_event)
        self._subscribed = True
        logger.debug(
            "CapacityWaiter subscribed to model.execution.completed and model.capacity.freed"
        )

    def _schedule_notify(self) -> None:
        """Schedule async notification (called from loop context)."""
        if self._loop and not self._loop.is_closed():
            self._loop.create_task(self._notify_execution_completion())

    async def _notify_execution_completion(self) -> None:
        """
        Notify all waiters of capacity change.

        Called on capacity events.
        """
        async with self._condition:
            self._epoch += 1
            self._condition.notify_all()
            logger.debug(f"Capacity event signaled (epoch={self._epoch})")

    async def wait_for_execution_completion(
        self, timeout_s: float | None = None
    ) -> None:
        """
        Wait for next capacity event.

        Uses epoch-based waiting to avoid race conditions:
        1. Capture current epoch
        2. Wait until epoch changes
        3. Return immediately if epoch already changed

        Args:
            timeout_s: Optional timeout in seconds. None = indefinite wait.

        Raises:
            TimeoutError: If timeout expires before capacity event
        """
        # Capture running loop for thread-safe event handler scheduling
        loop = asyncio.get_running_loop()
        self._subscribe_to_events(loop)

        start_epoch = self._epoch

        async with self._condition:
            # Wait until epoch changes (capacity event received)
            try:
                await asyncio.wait_for(
                    self._condition.wait_for(lambda: self._epoch > start_epoch),
                    timeout=timeout_s,
                )
                logger.debug(
                    f"Capacity event received (epoch {start_epoch} → {self._epoch})"
                )
            except TimeoutError:
                logger.debug(f"Capacity wait timeout after {timeout_s}s")
                raise


# Backward compatibility alias
ExecutionCompletionWaiter = CapacityWaiter
