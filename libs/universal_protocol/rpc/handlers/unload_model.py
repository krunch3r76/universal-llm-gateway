"""Unload model RPC handler."""

from universal_logging import get_logger
from typing import Any

from universal_protocol.errors import RPCError
from universal_protocol.observability import set_streams_active
from universal_protocol.ws.registry import stream_registry

from .log_format import make_log_prefix
from .model_state import LOADED_MODELS

logger = get_logger(__name__)


async def handle_unload_model(params: dict[str, Any]) -> dict[str, Any]:
    """Handle unload_model RPC method.

    Unloads model from memory and cleans up all active streams.

    Inputs:
        params: Method parameters containing:
            - name: Model identifier to unload
            - correlation_id: Optional correlation ID

    Outputs:
        Dict with success: bool

    Raises:
        RPCError: If name is missing
    """
    log_prefix = make_log_prefix(params)

    name = params.get("name")
    if not name:
        raise RPCError("INVALID_PARAMS", "name is required")

    if name not in LOADED_MODELS:
        logger.warning(f"{log_prefix} Model {name} is not loaded")
        return {"success": True}

    # Use registry's cancel_all_for_unload
    notified = stream_registry.cancel_all_for_unload(name)
    if notified > 0:
        logger.info(f"{log_prefix} Notified {notified} streams of unload")

    # Cleanup all entries
    cleaned = await stream_registry.cleanup_all()
    if cleaned > 0:
        logger.info(f"{log_prefix} Cleaned up {cleaned} entries")

    set_streams_active(len(stream_registry))

    del LOADED_MODELS[name]

    logger.info(f"{log_prefix} Model {name} unloaded successfully")
    return {"success": True}
