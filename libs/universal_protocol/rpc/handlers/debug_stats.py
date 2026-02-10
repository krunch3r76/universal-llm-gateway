"""Debug stats RPC handler."""

from universal_logging import get_logger
from typing import Any

from universal_protocol.observability import get_debug_stats

from .log_format import make_log_prefix

logger = get_logger(__name__)


async def handle_debug_stats(params: dict[str, Any]) -> dict[str, Any]:
    """Handle debug_stats RPC method.

    Returns current resource usage metrics for debugging.

    Inputs:
        params: Method parameters containing:
            - correlation_id: Optional correlation ID

    Outputs:
        Dict with fds_open and tasks_running
    """
    log_prefix = make_log_prefix(params)
    logger.debug(f"{log_prefix} Getting debug stats")
    return get_debug_stats()
