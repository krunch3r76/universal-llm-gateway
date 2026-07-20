"""Shared constants and debug helpers for model loading gate operations."""

from typing import Any

from universal_event_bus.events.debug import emit_debug_event

_LOAD_CACHE_TTL_S = 10.0
_ENGINE_READY_MAX_ATTEMPTS = 5
_ENGINE_READY_BACKOFF_S = 1.0


def get_resource_tracker():
    """Return the gateway singleton ResourceTracker used during model load gating."""
    from src.core.resources import resource_tracker

    return resource_tracker


async def emit_load_gate_debug(
    step: str, model_id: str, correlation_id: str | None = None, **extra: Any
) -> None:
    """Emit a debug.load.gate event with step, model_id, and optional correlation metadata."""
    await emit_debug_event(
        "debug.load.gate",
        {
            "step": step,
            "model_id": model_id,
            "correlation_id": correlation_id,
            **extra,
        },
        source="gateway",
    )
