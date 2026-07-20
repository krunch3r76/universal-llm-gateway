"""Shared lazy imports, loggers, and debug helpers for model load flow modules."""

from typing import Any

from universal_event_bus.events.debug import emit_debug_event
from universal_logging import get_logger

from ...state_machine import WorkerState

logger = get_logger(__name__)
structured_logger = get_logger("universal_llm_gateway.load_flow")

# Models with an in-flight failed-load cleanup; blocks concurrent loads until done.
_cleanup_in_progress: set[str] = set()


def get_resource_tracker():
    """Lazily imports and returns the global resource_tracker instance."""
    from src.core.resources import resource_tracker

    return resource_tracker


def get_event_classes():
    """Lazily imports and returns the event classes for model loading."""
    from src.core.events.types import (
        ModelLoaded,
        ModelLoadFailed,
        ModelLoadingProgress,
        ModelLoadingStarted,
    )

    return ModelLoadFailed, ModelLoaded, ModelLoadingProgress, ModelLoadingStarted


async def publish_event(event_bus, event) -> bool:
    """Publish event with error handling. Returns True if published."""
    if not event_bus:
        return False
    try:
        await event_bus.publish_nowait(event)
        return True
    except Exception as e:
        logger.warning(f"⚠️ Failed to publish event: {e}")
        return False


async def emit_load_flow_debug(step: str, model_id: str, **extra: Any) -> None:
    """Emit structured debug telemetry for load-flow step transitions."""
    await emit_debug_event(
        "debug.load.flow",
        {
            "step": step,
            "model_id": model_id,
            **extra,
        },
        source="gateway",
    )


def is_cleanup_in_progress(model_id: str) -> bool:
    """Return whether failed-load cleanup is currently running for model_id."""
    return model_id in _cleanup_in_progress


def mark_cleanup_started(model_id: str) -> None:
    """Record that failed-load cleanup has started for model_id."""
    _cleanup_in_progress.add(model_id)


def mark_cleanup_finished(model_id: str) -> None:
    """Clear in-flight failed-load cleanup marker for model_id."""
    _cleanup_in_progress.discard(model_id)


__all__ = [
    "WorkerState",
    "emit_load_flow_debug",
    "get_event_classes",
    "get_resource_tracker",
    "is_cleanup_in_progress",
    "logger",
    "mark_cleanup_finished",
    "mark_cleanup_started",
    "publish_event",
    "structured_logger",
]
