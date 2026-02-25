"""Handlers for system events (PING, RESOURCE_UPDATE, ERROR, SHUTDOWN, DRAINING)."""

import json
from typing import Any

from universal_logging import get_logger

from ..messages import create_pong_message
from .base import AsyncMessageHandler, SyncMessageHandler
from .context import HandlerContext

logger = get_logger(__name__)


class PingHandler(AsyncMessageHandler):
    """
    Handle PING message (keep-alive).

    I/O: send PONG response

    Note: This is the only handler that requires async for I/O.
    """

    async def handle(self, data: dict[str, Any], ctx: HandlerContext) -> None:
        if ctx.send_message is None:
            logger.warning("PingHandler: send_message not available")
            return

        pong = create_pong_message()
        await ctx.send_message(json.dumps(pong))


class ResourceUpdateHandler(SyncMessageHandler):
    """
    Handle RESOURCE_UPDATE message.

    State: update _resources, optionally _loaded_models
    Side effect: callback notification (fire-and-forget)
    """

    def handle(self, data: dict[str, Any], ctx: HandlerContext) -> None:
        self._apply_resource_update(data, ctx)
        self._sync_loaded_models(data, ctx)
        self._log_resource_state(ctx)
        self._notify_callback(data, ctx)

    def _apply_resource_update(self, data: dict[str, Any], ctx: HandlerContext) -> None:
        """Apply Gateway resource update (reservation-aware)."""
        ctx.update_resources_from_gateway(
            available_vram_mb=data.get("available_vram_mb"),
            available_ram_mb=data.get("available_ram_mb"),
        )

    def _sync_loaded_models(self, data: dict[str, Any], ctx: HandlerContext) -> None:
        """Sync loaded_models and per-model VRAM if provided (Gateway authoritative)."""
        if "loaded_models" in data:
            ctx.loaded_models.clear()
            ctx.loaded_models.update(data["loaded_models"])

        model_vram: dict[str, int] | None = data.get("model_vram")
        if model_vram:
            for model_id, vram_mb in model_vram.items():
                if model_id in ctx.model_details:
                    ctx.model_details[model_id]["vram_usage"] = vram_mb
                else:
                    ctx.model_details[model_id] = {
                        "vram_usage": vram_mb,
                        "ram_usage": 0,
                    }

    def _log_resource_state(self, ctx: HandlerContext) -> None:
        """Log current resource state after update."""
        models = list(ctx.loaded_models) if ctx.loaded_models else "NONE"
        logger.info(
            f"📥 Received RESOURCE_UPDATE from gateway: "
            f"available_vram={ctx.resources.available_vram_mb}MB "
            f"(total={ctx.resources.total_vram_mb}MB), "
            f"available_ram={ctx.resources.available_ram_mb}MB "
            f"(total={ctx.resources.total_ram_mb}MB), "
            f"loaded_models={models}"
        )

    def _notify_callback(self, data: dict[str, Any], ctx: HandlerContext) -> None:
        """Schedule callback notification (fire-and-forget)."""
        if ctx.on_resource_update:
            ctx.schedule_callback(ctx.on_resource_update, (data,))
        # Update resource timestamp (capacity changed)
        if ctx.on_resource_change:
            ctx.schedule_callback(ctx.on_resource_change, ())


class ErrorHandler(SyncMessageHandler):
    """
    Handle ERROR message.

    Side effect: logging only
    """

    def handle(self, data: dict[str, Any], ctx: HandlerContext) -> None:
        code = data.get("code", "unknown")
        error_msg = data.get("message", "Unknown error")
        logger.error(f"Gateway error: [{code}] {error_msg}")


class GatewayShutdownHandler(SyncMessageHandler):
    """
    Handle GATEWAY_SHUTDOWN message (gateway shutting down).

    This is received via WebSocket before the connection closes.
    Allows Stargate to cleanly handle the shutdown and prepare for reconnection.
    """

    def handle(self, data: dict[str, Any], ctx: HandlerContext) -> None:
        gateway_id = data.get("gateway_id", "unknown")
        reason = data.get("reason", "unknown")

        logger.info(
            f"Gateway {gateway_id} shutting down (reason={reason}), "
            f"connection will close shortly"
        )

        # Connection will close after this message
        # Reconnection will be triggered by message loop detecting ConnectionClosed


class GatewayDrainingHandler(SyncMessageHandler):
    """
    Handle GATEWAY_DRAINING message (gateway entering graceful shutdown).

    Signals that gateway is draining and will not accept new requests,
    but existing requests will complete.
    """

    def handle(self, data: dict[str, Any], ctx: HandlerContext) -> None:
        gateway_id = data.get("gateway_id", "unknown")
        reason = data.get("reason", "unknown")
        timeout = data.get("timeout", 30)

        logger.info(
            f"Gateway {gateway_id} draining (reason={reason}, timeout={timeout}s), "
            f"routing new requests elsewhere"
        )

        # Gateway will remain connected during drain period
        # No immediate action needed - routing layer handles this via events
