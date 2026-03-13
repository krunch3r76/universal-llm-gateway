"""Handlers for model availability events (IDLE, UNLOADED)."""

from typing import Any

from universal_logging import get_logger

from .base import SyncMessageHandler
from .context import HandlerContext

logger = get_logger(__name__)


class ModelIdleHandler(SyncMessageHandler):
    """
    Handle MODEL_IDLE message.

    State:
      - _busy_models.discard()
      - _model_last_inference[model_id] = last_inference_time
    Side effect: on_model_idle callback, wake queue processors (fire-and-forget)

    Invariant: MODEL_IDLE ⟹ model.capacity.freed scheduled
    Invariant: last_inference_time always present and cached
    """

    def handle(self, data: dict[str, Any], ctx: HandlerContext) -> None:
        model_id = data.get("model_id")
        if not model_id:
            logger.debug("MODEL_IDLE missing model_id")
            return

        ctx.busy_models.discard(model_id)
        ctx.busy_since.pop(model_id, None)

        timestamp = data.get("last_inference_time")
        if not isinstance(timestamp, int | float):
            logger.warning(
                "MODEL_IDLE for %s missing/invalid last_inference_time", model_id
            )
            _ = ctx.model_last_inference.pop(model_id, None)
        else:
            ctx.model_last_inference[model_id] = float(timestamp)

        logger.debug(f"Model idle on Gateway: {model_id}")

        # Fire callback for federation telemetry (includes last_inference_time)
        if ctx.on_model_idle:
            ctx.schedule_callback(ctx.on_model_idle, (model_id, data))

        # Wake queue processors (fire-and-forget)
        ctx.schedule_capacity_freed(model_id)

        # Update resource timestamp (capacity changed)
        if ctx.on_resource_change:
            ctx.schedule_callback(ctx.on_resource_change, ())


class ModelUnloadedHandler(SyncMessageHandler):
    """
    Handle MODEL_UNLOADED message.

    State:
      - _loaded_models.discard()
      - _model_last_inference.pop(model_id) (cleanup)
    Side effects:
      - callback notification (fire-and-forget)
      - wake queue processors (fire-and-forget)

    Invariant: MODEL_UNLOADED ⟹ model.capacity.freed scheduled
    """

    def handle(self, data: dict[str, Any], ctx: HandlerContext) -> None:
        model_id = data.get("model_id")
        if not model_id:
            logger.debug("MODEL_UNLOADED missing model_id")
            return

        ctx.loaded_models.discard(model_id)
        _ = ctx.loading_since.pop(model_id, None)  # Cleanup orphan loading timestamp
        _ = ctx.model_last_inference.pop(model_id, None)  # Cleanup cache
        _ = ctx.model_details.pop(model_id, None)  # Cleanup resource usage
        _ = ctx.measured_model_vram.pop(model_id, None)  # Cleanup measured VRAM
        logger.info(f"Model unloaded on Gateway: {model_id}")

        if ctx.on_model_unloaded:
            ctx.schedule_callback(ctx.on_model_unloaded, (model_id,))

        # Wake queue processors (fire-and-forget)
        ctx.schedule_capacity_freed(model_id)

        # Update resource timestamp (capacity changed)
        if ctx.on_resource_change:
            ctx.schedule_callback(ctx.on_resource_change, ())
