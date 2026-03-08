"""Event emission helpers for resource tracking.

Fire-and-forget event publishing using EventBus native async API.
"""

import os
import socket
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


def _resolve_gateway_identity() -> str:
    """Resolve gateway identity for request-scoped telemetry.

    Uses GATEWAY_NAME env var (set by ./manage), falls back to hostname.
    """
    return os.environ.get("GATEWAY_NAME", socket.gethostname())


async def emit_inference_started(
    event_bus: Any, model_id: str, request_id: str = ""
) -> None:
    """Emit inference-started events (fire-and-forget).

    Emits two events:
    - InferenceStarted (model-scoped): for aggregation.py, resource_monitor.py
    - RequestInferenceStarted (request-scoped): forwarded to Stargate via WebSocket
      so pipeline timeout diagnostics can distinguish queued vs executing

    Uses EventBus publish_async_nowait for non-blocking emission.
    """
    if not event_bus:
        return
    try:
        from src.core.events.types import InferenceStarted, RequestInferenceStarted

        logger.debug(f"🔔 Emitting INFERENCE_STARTED for {model_id}")
        await event_bus.publish_async_nowait(InferenceStarted(model_id=model_id))

        if request_id:
            gateway_url = _resolve_gateway_identity()
            logger.debug(
                f"🔔 Emitting REQUEST_INFERENCE_STARTED for {model_id} "
                f"request_id={request_id}"
            )
            await event_bus.publish_async_nowait(
                RequestInferenceStarted(
                    request_id=request_id,
                    model_id=model_id,
                    gateway_url=gateway_url,
                )
            )
    except Exception as e:
        logger.warning(f"Failed to emit inference started events: {e}")


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
