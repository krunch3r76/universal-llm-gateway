"""Handler for TELEMETRY_HEARTBEAT messages."""

from typing import Any

from universal_logging import get_logger

from .base import SyncMessageHandler
from .context import HandlerContext

logger = get_logger(__name__)


class TelemetryHeartbeatHandler(SyncMessageHandler):
    """
    Handle TELEMETRY_HEARTBEAT message.

    Updates heartbeat timestamp without modifying resource state.
    Proves telemetry path is working, nothing more.

    Invariant: ∀ heartbeat ⟹ last_heartbeat updated ∧ resources unchanged
    """

    def handle(self, data: dict[str, Any], ctx: HandlerContext) -> None:
        """Process heartbeat - update heartbeat timestamp only."""
        gateway_id = data.get("gateway_id", "unknown")

        # Schedule callback to update heartbeat timestamp
        # (Non-blocking: callback scheduled for later execution)
        if ctx.on_heartbeat:
            ctx.schedule_callback(ctx.on_heartbeat, ())

        # Also notify external callback (for federation forwarding)
        if ctx.on_telemetry_heartbeat:
            ctx.schedule_callback(ctx.on_telemetry_heartbeat, (data,))

        logger.debug(f"💓 Received TELEMETRY_HEARTBEAT from {gateway_id}")
