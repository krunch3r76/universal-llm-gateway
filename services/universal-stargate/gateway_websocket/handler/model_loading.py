"""Handlers for model loading lifecycle events."""

import time
from typing import Any

from model_id import ModelId
from universal_logging import get_logger

from .base import SyncMessageHandler
from .context import HandlerContext

logger = get_logger(__name__)


def _extract_routing_key(model_id: str) -> str | None:
    """
    Extract routing_key from model_id for callback lookup.

    Returns None if model_id cannot be parsed (logs warning).
    """
    try:
        return ModelId.parse(model_id).routing_key
    except ValueError:
        logger.warning(f"Failed to parse model_id for callback lookup: {model_id}")
        return None


class ModelLoadingStartedHandler(SyncMessageHandler):
    """
    Handle MODEL_LOADING_STARTED message.

    State: _loading_models.add(model_id)
    Side effect: callback notification (fire-and-forget)
    """

    def handle(self, data: dict[str, Any], ctx: HandlerContext) -> None:
        model_id = data.get("model_id")
        if not model_id:
            logger.debug("MODEL_LOADING_STARTED missing model_id")
            return

        # Guard: if model is already loaded, ignore spurious loading event.
        # Prevents re-load attempts from defeating the TTL watchdog.
        if model_id in ctx.loaded_models:
            logger.info(
                f"MODEL_LOADING_STARTED for {model_id} ignored — "
                "already in loaded_models (stale re-load attempt)"
            )
            return

        ctx.loading_models.add(model_id)
        ctx.loading_since[model_id] = time.monotonic()
        logger.info(f"Model loading started on Gateway: {model_id}")

        if ctx.on_model_loading_started:
            ctx.schedule_callback(ctx.on_model_loading_started, (model_id,))


class ModelLoadedHandler(SyncMessageHandler):
    """
    Handle MODEL_LOADED message.

    State: _loaded_models.add(), _loading_models.discard()
    Side effect: callback notification (fire-and-forget)

    Dispatch order:
    1. Model-specific callback (if registered) - for LoadOutcomeTracker
    2. Global callback - for EdgeFederationServer telemetry forwarding
    """

    def handle(self, data: dict[str, Any], ctx: HandlerContext) -> None:
        model_id = data.get("model_id")
        if not model_id:
            logger.debug("MODEL_LOADED missing model_id")
            return

        ctx.loaded_models.add(model_id)
        ctx.loading_models.discard(model_id)
        _ = ctx.loading_since.pop(model_id, None)

        # Store resource usage for eviction planning
        vram_mb = data.get("vram_mb", 0)
        ram_mb = data.get("ram_mb", 0)
        ctx.model_details[model_id] = {
            "vram_usage": vram_mb,
            "ram_usage": ram_mb,
        }

        logger.info(
            f"Model loaded on Gateway: {model_id} (VRAM={vram_mb}MB, RAM={ram_mb}MB)"
        )

        # Dispatch to model-specific callbacks first (LoadOutcomeTracker)
        # Multiple trackers may be waiting for the same model
        # Copy set to prevent RuntimeError from concurrent registrations
        routing_key = _extract_routing_key(model_id)
        if routing_key and routing_key in ctx.model_loaded_callbacks:
            for callback in list(ctx.model_loaded_callbacks[routing_key]):
                ctx.schedule_callback(callback, (model_id, data))

        # Dispatch to global callback (EdgeFederationServer telemetry)
        if ctx.on_model_loaded:
            ctx.schedule_callback(ctx.on_model_loaded, (model_id, data))

        # Update resource timestamp (capacity changed)
        if ctx.on_resource_change:
            ctx.schedule_callback(ctx.on_resource_change, ())


class ModelLoadFailedHandler(SyncMessageHandler):
    """
    Handle MODEL_LOAD_FAILED message.

    State: _loading_models.discard()
    Side effect: callback notification (fire-and-forget)

    Dispatch order:
    1. Model-specific callback (if registered) - for LoadOutcomeTracker
    2. Global callback - for EdgeFederationServer telemetry forwarding
    """

    def handle(self, data: dict[str, Any], ctx: HandlerContext) -> None:
        model_id = data.get("model_id")
        error_message = data.get("error_message", "Unknown error")
        if not model_id:
            logger.debug("MODEL_LOAD_FAILED missing model_id")
            return

        ctx.loading_models.discard(model_id)
        _ = ctx.loading_since.pop(model_id, None)
        logger.error(f"Model load failed on Gateway: {model_id}: {error_message}")

        # Dispatch to model-specific callbacks first (LoadOutcomeTracker)
        # Multiple trackers may be waiting for the same model
        # Copy set to prevent RuntimeError from concurrent registrations
        routing_key = _extract_routing_key(model_id)
        if routing_key and routing_key in ctx.model_load_failed_callbacks:
            for callback in list(ctx.model_load_failed_callbacks[routing_key]):
                ctx.schedule_callback(callback, (model_id, error_message))

        # Dispatch to global callback (EdgeFederationServer telemetry)
        if ctx.on_model_load_failed:
            ctx.schedule_callback(ctx.on_model_load_failed, (model_id, error_message))


class ModelBusyHandler(SyncMessageHandler):
    """
    Handle MODEL_BUSY message.

    State: _busy_models.add()
    Side effect: on_model_busy callback, resource timestamp update
    """

    def handle(self, data: dict[str, Any], ctx: HandlerContext) -> None:
        model_id = data.get("model_id")
        if not model_id:
            logger.debug("MODEL_BUSY missing model_id")
            return

        ctx.busy_models.add(model_id)
        ctx.busy_since[model_id] = time.monotonic()
        logger.info(f"📥 MODEL_BUSY received: model={model_id}")

        # Fire callback for federation telemetry
        if ctx.on_model_busy:
            ctx.schedule_callback(ctx.on_model_busy, (model_id,))

        # Update resource timestamp (capacity changed)
        if ctx.on_resource_change:
            ctx.schedule_callback(ctx.on_resource_change, ())
