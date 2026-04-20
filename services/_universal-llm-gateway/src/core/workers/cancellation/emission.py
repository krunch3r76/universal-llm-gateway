"""
Event emission helpers for stream cancellation.

Single-responsibility functions for building, emitting, and fallback handling
of STREAM_CANCELLED events.
"""

from universal_logging import get_logger

logger = get_logger(__name__)


def _get_resource_tracker():
    """Lazy import to avoid circular dependency."""
    from src.core.resources import resource_tracker

    return resource_tracker


def _get_event_classes():
    """Lazy import of event classes."""
    from src.core.events import STREAM_CANCELLED, Event

    return STREAM_CANCELLED, Event


def build_stream_cancelled_event(
    model_id: str,
    stream_id: str | None,
    reason: str,
    source: str = "worker_controller",
):
    """
    Build STREAM_CANCELLED event payload.

    Args:
        model_id: Model identifier
        stream_id: Stream ID (None if unknown/current)
        reason: Cancellation reason
        source: Event source identifier

    Returns:
        Event instance ready for publishing
    """
    stream_cancelled, event_cls = _get_event_classes()

    # Use explicit stream_id or "current" for cancel-current-stream operations
    effective_stream_id = stream_id if stream_id is not None else "current"

    return event_cls(
        signal=stream_cancelled,
        payload={
            "model_id": model_id,
            "stream_id": effective_stream_id,
            "reason": reason,
            "source": source,
        },
    )


async def emit_stream_cancelled_nowait(event, event_bus) -> None:
    """
    Emit STREAM_CANCELLED event (fire-and-forget, non-blocking).

    Args:
        event: Event to publish
        event_bus: EventBus instance

    Raises:
        RuntimeError: If event_bus is None or publish fails
    """
    if not event_bus:
        raise RuntimeError("Event bus not available")

    await event_bus.publish_nowait(event)


async def fallback_force_idle_on_event_failure(model_id: str, reason: str) -> None:
    """
    Fallback: force model idle when event emission fails.

    Args:
        model_id: Model identifier
        reason: Reason for fallback

    Raises:
        RuntimeError: If fallback also fails
    """
    tracker = _get_resource_tracker()
    success = await tracker.force_model_idle(model_id, f"event_failed_{reason}")

    if not success:
        raise RuntimeError(
            f"force_model_idle() returned False for {model_id} - "
            f"model stuck in invalid state"
        )

    logger.warning(
        f"⚠️ Event emission failed, fallback force_model_idle succeeded for {model_id}"
    )


async def emit_stream_cancelled_or_force_idle(
    model_id: str,
    stream_id: str | None,
    reason: str,
    *,
    event_bus,
    source: str = "worker_controller",
) -> None:
    """
    Emit STREAM_CANCELLED event with automatic fallback to force_model_idle.

    Single unified operation for: build event → publish → fallback on failure.
    Reduces duplication across cancellation call sites.

    Args:
        model_id: Model identifier
        stream_id: Stream ID (None if unknown/current)
        reason: Cancellation reason
        event_bus: EventBus instance for publishing
        source: Event source identifier

    Raises:
        RuntimeError: If both event emission and fallback fail
    """
    try:
        event = build_stream_cancelled_event(model_id, stream_id, reason, source)
        await emit_stream_cancelled_nowait(event, event_bus)
        logger.info(f"📢 Emitted STREAM_CANCELLED for {model_id}")
    except Exception as e:
        logger.warning(f"⚠️ Event emission failed for {model_id}: {e}, using fallback")
        await fallback_force_idle_on_event_failure(model_id, reason)
