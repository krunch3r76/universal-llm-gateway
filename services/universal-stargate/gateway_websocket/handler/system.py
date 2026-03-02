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

    def __init__(self) -> None:
        self._catalog_structure_warned: bool = False

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
        """Sync loaded_models, measured VRAM, and check for catalog drift."""
        if "loaded_models" in data:
            newly_loaded = set(data["loaded_models"])

            # Prune stale measured state using authoritative loaded_models.
            stale_measured = set(ctx.measured_model_vram.keys()) - newly_loaded
            for model_id in stale_measured:
                _ = ctx.measured_model_vram.pop(model_id, None)
                _ = ctx.model_details.pop(model_id, None)

            ctx.loaded_models.clear()
            ctx.loaded_models.update(newly_loaded)

        model_vram: dict[str, int] | None = data.get("model_vram")
        if not model_vram:
            return

        missing_key = "model_resources" not in ctx.catalog
        if missing_key and not self._catalog_structure_warned:
            logger.warning(
                f"VRAM drift detection disabled: 'model_resources' absent from catalog "
                f"for gateway {ctx.gateway_name}"
            )
            self._catalog_structure_warned = True
        catalog_resources: dict[str, Any] = ctx.catalog.get("model_resources", {})

        for model_id, measured_mb in model_vram.items():
            # Update measured-only store (partial update — preserve other models)
            ctx.measured_model_vram[model_id] = measured_mb

            # Update mixed model_details for backward compatibility
            if model_id in ctx.model_details:
                ctx.model_details[model_id]["vram_usage"] = measured_mb
            else:
                ctx.model_details[model_id] = {
                    "vram_usage": measured_mb,
                    "ram_usage": 0,
                }

            # Drift check: compare measured against catalog (1h cooldown per model)
            catalog_mb: int | None = catalog_resources.get(model_id, {}).get(
                "vram_usage"
            )
            if catalog_mb and catalog_mb > 0:
                drift_pct = abs(measured_mb - catalog_mb) / catalog_mb * 100.0
                if drift_pct > 5.0 and ctx.can_report_vram_drift(model_id):
                    self._handle_vram_drift(
                        ctx, model_id, measured_mb, catalog_mb, drift_pct
                    )

    def _handle_vram_drift(
        self,
        ctx: HandlerContext,
        model_id: str,
        measured_mb: int,
        catalog_mb: int,
        drift_pct: float,
    ) -> None:
        """Log warning and schedule drift event.
        Triggered when measured VRAM diverges from catalog by >5%.
        """
        logger.warning(
            f"VRAM drift detected: {model_id} on {ctx.gateway_name} — "
            f"measured={measured_mb}MB, catalog={catalog_mb}MB, drift={drift_pct:.1f}%"
        )
        if ctx.on_vram_drift:
            ctx.schedule_callback(
                ctx.on_vram_drift, (model_id, measured_mb, catalog_mb, drift_pct)
            )

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
