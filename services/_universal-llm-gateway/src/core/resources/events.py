"""Event emission helpers for resource tracking.

Fire-and-forget event publishing using EventBus native async API.
"""

from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


async def emit_inference_started(event_bus: Any, model_id: str) -> None:
    """Emit INFERENCE_STARTED event (fire-and-forget).

    Uses EventBus publish_async_nowait for non-blocking emission.
    """
    if not event_bus:
        return
    try:
        from src.core.events.types import InferenceStarted

        logger.debug(f"🔔 Emitting INFERENCE_STARTED for {model_id}")
        await event_bus.publish_async_nowait(InferenceStarted(model_id=model_id))
    except Exception as e:
        logger.warning(f"Failed to emit INFERENCE_STARTED: {e}")


async def emit_inference_completed(
    event_bus: Any, model_id: str, last_inference_time: float
) -> None:
    """Emit INFERENCE_COMPLETED event with last_inference_time (fire-and-forget).

    Uses EventBus publish_async_nowait for non-blocking emission.

    Args:
        event_bus: EventBus instance for event emission
        model_id: Model identifier
        last_inference_time: Unix timestamp when inference completed
    """
    if not event_bus:
        return
    try:
        from src.core.events.types import InferenceCompleted

        logger.debug(f"🔔 Emitting INFERENCE_COMPLETED for {model_id}")
        await event_bus.publish_async_nowait(
            InferenceCompleted(
                model_id=model_id, last_inference_time=last_inference_time
            )
        )
    except Exception as e:
        logger.warning(f"Failed to emit INFERENCE_COMPLETED: {e}")
