"""Package-private runtime helpers and loggers for the worker controller."""

from typing import Any

from universal_event_bus.events.debug import emit_debug_event
from universal_logging import get_logger


def _get_resource_tracker():
    from src.core.resources import resource_tracker

    return resource_tracker


logger = get_logger(__name__)
structured_logger = get_logger("universal_llm_gateway.controller")


async def _emit_embedding_debug(
    step: str,
    model_id: str,
    correlation_id: str | None,
    **extra: Any,
) -> None:
    """Emit a temporary debug event for embedding request tracing."""
    payload: dict[str, Any] = {
        "step": step,
        "component": "controller",
        "model_id": model_id,
    }
    if correlation_id:
        payload["correlation_id"] = correlation_id
    payload.update(extra)
    await emit_debug_event("debug.embedding.gateway", payload, source="gateway")
