"""Request-scoped event emission helpers.

Non-blocking event publishing using EventBus publish_nowait.

Contract: event_bus is a hard dependency - fail fast if missing.
"""

from universal_event_bus import EventBus
from universal_logging import get_logger

from src.core.events.types import InferenceFailed, RequestQueued

logger = get_logger(__name__)


async def emit_request_queued_nowait(
    event_bus: EventBus,
    model_id: str,
    request_id: str,
    messages,
    parameters: dict,
    stream: bool,
) -> None:
    """
    Emit REQUEST_QUEUED event (fire-and-forget).

    Args:
        event_bus: EventBus instance (required)
        model_id: Model handling the request
        request_id: Request tracking ID
        messages: Request messages
        parameters: Request parameters
        stream: Whether streaming is enabled

    Raises:
        ValueError: If event_bus is None
    """
    if event_bus is None:
        raise ValueError("event_bus is required for request event emission")

    await event_bus.publish_nowait(
        RequestQueued(
            model_id=model_id,
            request_id=request_id,
            messages=messages,
            parameters=parameters,
            stream=stream,
        )
    )


async def emit_inference_failed_nowait(
    event_bus: EventBus,
    model_id: str,
    request_id: str,
    error_message: str,
) -> None:
    """
    Emit INFERENCE_FAILED event (fire-and-forget).

    Args:
        event_bus: EventBus instance (required)
        model_id: Model that failed
        request_id: Request tracking ID
        error_message: Error message

    Raises:
        ValueError: If event_bus is None
    """
    if event_bus is None:
        raise ValueError("event_bus is required for failure event emission")

    await event_bus.publish_nowait(
        InferenceFailed(
            model_id=model_id,
            request_id=request_id,
            error_message=error_message,
        )
    )
