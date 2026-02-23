"""
Event consumer for capacity release.

Subscribes to MODEL_EXECUTION_COMPLETED and calls ledger.release().
This is the canonical release point for admission control.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

if TYPE_CHECKING:
    from .ledger import CapacityLedger
    from .queue import AdmissionQueue

logger = get_logger(__name__)


class CapacityReleaseConsumer:
    """Event consumer for MODEL_EXECUTION_COMPLETED. Releases slots and wakes queue."""

    def __init__(
        self,
        ledger: CapacityLedger,
        queue: AdmissionQueue | None = None,
        event_bus: Any | None = None,
    ) -> None:
        self._ledger = ledger
        self._queue = queue
        self._event_bus = event_bus

    def start(self) -> None:
        """Subscribe to MODEL_EXECUTION_COMPLETED and MODEL_EXECUTION_FAILED events."""
        if not self._event_bus:
            logger.warning("No event_bus, capacity release consumer not started")
            return
        from src.scheduling.events import MODEL_EXECUTION_COMPLETED, MODEL_EXECUTION_FAILED

        self._event_bus.subscribe_async(
            MODEL_EXECUTION_COMPLETED, self._on_execution_completed
        )
        self._event_bus.subscribe_async(
            MODEL_EXECUTION_FAILED, self._on_execution_completed
        )
        logger.info("CapacityReleaseConsumer started")

    async def _on_execution_completed(self, event) -> None:
        """Handle MODEL_EXECUTION_COMPLETED: release slot and wake queue."""
        request_id = event.payload.get("request_id")
        model_id = event.payload.get("model_id")
        if not request_id:
            logger.error("MODEL_EXECUTION_COMPLETED missing request_id")
            return
        released = self._ledger.release(request_id)
        if released:
            logger.debug(f"Released capacity: {request_id}")
        else:
            logger.warning(f"No reservation found for {request_id}")
        if model_id and self._queue:
            await self._queue._dispatch(model_id)
