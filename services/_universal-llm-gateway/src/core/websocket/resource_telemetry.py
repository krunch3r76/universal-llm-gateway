"""Periodic resource telemetry publisher for WebSocket connections."""

import asyncio
from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from ..resources.tracker import ResourceTracker

logger = get_logger(__name__)


class ResourceTelemetryPublisher:
    """
    Publishes periodic RESOURCE_UPDATE events to keep Stargate telemetry fresh.

    Triggers get_system_resources() at regular intervals, which publishes
    SYSTEM_RESOURCES_UPDATED events via EventBus → WebSocketEventForwarder.

    Ensures idle gateways maintain fresh telemetry for federated routing.

    Invariant: ∀ idle_period ≥ interval ⟹ ≥1 RESOURCE_UPDATE sent
    """

    def __init__(
        self,
        resource_tracker: "ResourceTracker",
        interval_seconds: float = 5.0,
    ):
        self._resource_tracker = resource_tracker
        self._interval = interval_seconds
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        """Start periodic resource telemetry publishing."""
        if self._running:
            logger.warning("Resource telemetry publisher already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._publish_loop())
        logger.info(
            f"✅ Started resource telemetry publisher (interval={self._interval}s)"
        )

    async def stop(self) -> None:
        """Stop periodic resource telemetry publishing."""
        if not self._running:
            return

        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        logger.info("Stopped resource telemetry publisher")

    async def _publish_loop(self) -> None:
        """Background loop that publishes resource telemetry."""
        while self._running:
            try:
                await asyncio.sleep(self._interval)

                # Trigger resource update via existing mechanism
                # get_system_resources() publishes SYSTEM_RESOURCES_UPDATED event
                await self._resource_tracker.get_system_resources()

                logger.debug(
                    "📊 Published periodic resource telemetry "
                    f"(interval={self._interval}s)"
                )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in resource telemetry loop: {e}", exc_info=True)
