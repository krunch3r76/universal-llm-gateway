"""
Epoch-based telemetry freshness waiter.

Invariant: ∀ GATEWAY_RESOURCE_UPDATE events are captured (no missed signals)
Pattern: Epoch counter + asyncio.Condition (not Event.clear())
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


class TelemetryFreshnessWaiter:
    """
    Wait for telemetry updates without race conditions.

    Uses epoch counter to ensure no signals are missed between
    checking and waiting.

    Invariant: ∀ GATEWAY_RESOURCE_UPDATE ⟹ epoch incremented ⟹ waiters notified
    """

    def __init__(self, event_bus: Any = None) -> None:
        """
        Initialize waiter.

        Args:
            event_bus: Event bus for GATEWAY_RESOURCE_UPDATE events
        """
        self._event_bus = event_bus
        self._condition = asyncio.Condition()
        self._epoch = 0  # Monotonically increasing on each RESOURCE_UPDATE
        self._subscribed = False
        self._loop: asyncio.AbstractEventLoop | None = None

    def _subscribe_to_events(self, loop: asyncio.AbstractEventLoop) -> None:
        """Subscribe to GATEWAY_RESOURCE_UPDATE events (lazy subscription)."""
        if self._loop is not loop:
            self._loop = loop
            logger.debug(f"TelemetryFreshnessWaiter loop rebound (id={id(loop)})")

        if self._subscribed or not self._event_bus:
            return

        from src.scheduling.events import GATEWAY_RESOURCE_UPDATE

        async def on_resource_update(_event) -> None:
            """Increment epoch and notify all waiters."""
            # Called by event bus in async context - directly notify
            await self._notify_update()

        self._event_bus.subscribe_async(GATEWAY_RESOURCE_UPDATE, on_resource_update)
        self._subscribed = True
        logger.debug("TelemetryFreshnessWaiter subscribed to GATEWAY_RESOURCE_UPDATE")

    async def _notify_update(self) -> None:
        """Notify all waiters of update (called on GATEWAY_RESOURCE_UPDATE)."""
        async with self._condition:
            self._epoch += 1
            self._condition.notify_all()
            logger.debug(f"Telemetry freshness signaled (epoch={self._epoch})")

    async def wait_for_telemetry_update(self, timeout_s: float = 0.5) -> bool:
        """
        Wait for next GATEWAY_RESOURCE_UPDATE event.

        Uses epoch-based waiting to avoid race conditions:
        1. Capture current epoch
        2. Wait until epoch changes
        3. Return immediately if epoch already changed

        Note: This waits for "next update event", not "freshness" semantically.
        The telemetry may still be stale if the gateway hasn't sent recent data.

        Args:
            timeout_s: Timeout in seconds (default 500ms)

        Returns:
            True if update received, False if timeout
        """
        loop = asyncio.get_running_loop()
        self._subscribe_to_events(loop)

        start_epoch = self._epoch

        async with self._condition:
            try:
                await asyncio.wait_for(
                    self._condition.wait_for(lambda: self._epoch > start_epoch),
                    timeout=timeout_s,
                )
                logger.debug(
                    f"Telemetry freshness received "
                    f"(epoch {start_epoch} → {self._epoch})"
                )
                return True
            except TimeoutError:
                logger.debug(f"Telemetry freshness timeout after {timeout_s}s")
                return False
