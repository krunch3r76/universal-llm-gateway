"""Health RPC handler."""

from typing import Any

from universal_protocol.ws.registry import stream_registry

from .model_state import LOADED_MODELS


async def handle_health(params: dict[str, Any]) -> dict[str, Any]:
    """Handle health RPC method.

    Returns current health status including loaded models and active streams.

    Inputs:
        params: Method parameters (unused but required by RPC interface)

    Outputs:
        Dict with status, models list, active_streams count
    """
    status = "ready" if LOADED_MODELS else "busy"
    models = list(LOADED_MODELS.keys())
    active_stream_count = len(stream_registry)

    return {
        "status": status,
        "models": models,
        "active_streams": active_stream_count,
    }
