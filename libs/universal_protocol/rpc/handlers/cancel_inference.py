"""Cancel inference RPC handler."""

from typing import Any

from universal_logging import get_logger

from universal_protocol.errors import RPCError
from universal_protocol.observability import set_streams_active
from universal_protocol.ws.registry import stream_registry

from .log_format import make_log_prefix

logger = get_logger(__name__)


async def handle_cancel_inference(params: dict[str, Any]) -> dict[str, Any]:
    """Handle cancel_inference RPC method.

    Cancels an active streaming inference request.

    Inputs:
        params: Method parameters containing:
            - stream_id: ID of the stream to cancel
            - correlation_id: Optional correlation ID

    Outputs:
        Dict with success: bool

    Raises:
        RPCError: If stream_id is missing
    """
    log_prefix = make_log_prefix(params)

    stream_id = params.get("stream_id")
    if not stream_id:
        raise RPCError("INVALID_PARAMS", "stream_id is required")

    # Use registry's cancel_entry
    found = stream_registry.cancel_entry(stream_id, reason="client_cancelled")

    if found:
        logger.info(f"{log_prefix} Cancelled stream {stream_id}")
        await stream_registry.cleanup_entry(stream_id)
    else:
        logger.warning(f"{log_prefix} Stream {stream_id} not found")

    set_streams_active(len(stream_registry))
    return {"success": True}
