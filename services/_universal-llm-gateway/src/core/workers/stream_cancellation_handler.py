"""
Event-driven stream cancellation handler.

This module provides event-driven handling of stream cancellation events.
It subscribes to STREAM_CANCELLED events and automatically marks models
as idle when streams are cancelled.
"""

from universal_logging import get_logger

from src.core.events import STREAM_CANCELLED, Event, EventBus

logger = get_logger(__name__)


class StreamCancellationHandler:
    """
    Event-driven handler for stream cancellation events.

    Subscribes to STREAM_CANCELLED events and automatically marks models
    as idle when streams are cancelled, ensuring the resource tracker
    accurately reflects model availability.

    Edge cases handled:
    - Multiple concurrent cancellations: Handler is async-safe
    - Cancellations during different states: force_model_idle handles any state
    - Missing model_id: Logged and skipped
    - Resource tracker errors: Caught and logged, don't block other handlers
    """

    def __init__(self, event_bus: EventBus):
        """
        Initialize the stream cancellation handler.

        Args:
            event_bus: EventBus instance for subscribing to events
        """
        self.event_bus = event_bus
        self._cancellation_count = 0  # Track total cancellations for monitoring

        # Subscribe to cancellation events (EventBus auto-detects async)
        self.event_bus.subscribe_async(STREAM_CANCELLED, self._handle_stream_cancellation)

        logger.info("🔄 StreamCancellationHandler initialized")

    async def _handle_stream_cancellation(self, event: Event):
        """
        Handle stream cancellation events by marking models as idle.

        This handler ensures that cancelled streams immediately mark the
        model as idle in the resource tracker, allowing new requests to be
        properly routed to available models.

        State transitions handled:
        - BUSY → LOADED (cancelled during inference)
        - ERROR → LOADED (cancelled in error state, recovery)
        - Any state → LOADED (force idle for any cancellation)

        Args:
            event: Event containing cancellation details
        """
        model_id = event.payload.get("model_id")
        stream_id = event.payload.get("stream_id", "unknown")
        reason = event.payload.get("reason", "unknown")
        source = event.payload.get("source", "unknown")

        if not model_id:
            logger.warning("Stream cancellation event missing model_id")
            return

        self._cancellation_count += 1
        logger.info(
            f"🔄 Handling stream cancellation for {model_id} (cancellation #{self._cancellation_count})"
        )
        logger.debug(
            f"Cancellation details: stream_id={stream_id}, reason={reason}, source={source}"
        )

        try:
            # Import resource tracker (lazy import to avoid circular dependencies)
            from src.core.resources import resource_tracker

            # Get current model state for debugging
            current_info = resource_tracker.get_model_info(model_id)
            if current_info:
                logger.debug(
                    f"Model {model_id} cancelled in state: {current_info.status}"
                )

            # Force model to idle state - this handles any current state
            # and ensures the model becomes available for new requests
            success = await resource_tracker.force_model_idle(
                model_id, f"stream_cancelled_{reason}"
            )

            if success:
                logger.info(
                    f"✅ Model {model_id} marked as idle after stream cancellation"
                )
            else:
                logger.warning(
                    "⚠️ force_model_idle failed for %s — model is in a state "
                    "where forcing LOADED is invalid (SM=%s). "
                    "State will resolve on next load or cleanup.",
                    model_id,
                    resource_tracker.get_state_machine_state(model_id),
                )

        except Exception as e:
            logger.error(
                f"❌ Failed to handle stream cancellation for {model_id}: {e}",
                exc_info=True,
            )

    def get_cancellation_count(self) -> int:
        """Get total number of cancellations handled (for monitoring)."""
        return self._cancellation_count
