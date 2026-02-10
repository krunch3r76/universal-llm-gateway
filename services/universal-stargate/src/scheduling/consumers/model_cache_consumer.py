"""
Model cache consumer that updates cache based on model state events.

Subscribes to MODEL_LOADED and MODEL_UNLOADED events to maintain
real-time model availability without periodic polling.
"""

import time
from typing import TYPE_CHECKING, Any

from universal_event_bus import Event, EventBus
from universal_logging import get_logger

from ..events import MODEL_LOADED, MODEL_UNLOADED

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


class ModelCacheConsumer:
    """
    Consumes model load/unload events and updates the model cache.

    This eliminates the need for periodic full cache refreshes by
    maintaining cache state incrementally based on events.

    Compatible with both SingleGatewayManager and MultiGatewayManager.
    """

    def __init__(
        self,
        event_bus: EventBus,
        gateway_manager: Any,  # SingleGatewayManager or MultiGatewayManager
    ):
        self.event_bus: EventBus = event_bus
        self.gateway_manager: Any = gateway_manager

    def start(self) -> None:
        """Start consuming model state events."""
        self.event_bus.subscribe_async(MODEL_LOADED, self._handle_model_loaded)
        self.event_bus.subscribe_async(MODEL_UNLOADED, self._handle_model_unloaded)
        logger.info("✅ ModelCacheConsumer started")

    def stop(self) -> None:
        """Stop consuming events."""
        logger.info("ModelCacheConsumer stopped")

    async def _handle_model_loaded(self, event: Event) -> None:
        """Handle MODEL_LOADED event - add model to gateway's available models."""
        payload = event.payload
        gateway_url = payload.get("url")
        model_id = payload.get("model_id")

        if not gateway_url or not model_id:
            logger.warning(f"Invalid MODEL_LOADED payload: {payload}")
            return

        self._add_model_to_gateway(gateway_url, model_id)

        logger.debug(f"📥 Cache updated: {model_id} loaded on {gateway_url}")

    async def _handle_model_unloaded(self, event: Event) -> None:
        """Handle MODEL_UNLOADED event - remove model from gateway's available models."""
        payload = event.payload
        gateway_url = payload.get("url")
        model_id = payload.get("model_id")

        if not gateway_url or not model_id:
            logger.warning(f"Invalid MODEL_UNLOADED payload: {payload}")
            return

        self._remove_model_from_gateway(gateway_url, model_id)

        logger.debug(f"📤 Cache updated: {model_id} unloaded from {gateway_url}")

    def _add_model_to_gateway(self, gateway_url: str, model_id: str) -> None:
        """Add model to gateway's available models in _model_to_gateway mapping."""
        # Check if gateway_manager has _model_to_gateway (MultiGatewayManager)
        # or is SingleGatewayManager (which doesn't need this mapping)
        if not hasattr(self.gateway_manager, "_model_to_gateway"):
            # SingleGatewayManager: WebSocket already tracks loaded models
            # Just touch cache timestamp to indicate freshness
            if hasattr(self.gateway_manager, "_cache_timestamp"):
                self.gateway_manager._cache_timestamp = time.time()
            return

        # MultiGatewayManager case
        model_to_gateway = self.gateway_manager._model_to_gateway

        if model_id not in model_to_gateway:
            model_to_gateway[model_id] = set()

        model_to_gateway[model_id].add(gateway_url)

        # Touch cache timestamp to indicate freshness
        self.gateway_manager._cache_timestamp = time.time()

    def _remove_model_from_gateway(self, gateway_url: str, model_id: str) -> None:
        """Remove model from gateway's available models in _model_to_gateway mapping."""
        # Check if gateway_manager has _model_to_gateway (MultiGatewayManager)
        # or is SingleGatewayManager (which doesn't need this mapping)
        if not hasattr(self.gateway_manager, "_model_to_gateway"):
            # SingleGatewayManager: WebSocket already tracks loaded models
            # Just touch cache timestamp to indicate freshness
            if hasattr(self.gateway_manager, "_cache_timestamp"):
                self.gateway_manager._cache_timestamp = time.time()
            return

        # MultiGatewayManager case
        model_to_gateway = self.gateway_manager._model_to_gateway

        if model_id in model_to_gateway:
            model_to_gateway[model_id].discard(gateway_url)
            # Clean up empty sets
            if not model_to_gateway[model_id]:
                del model_to_gateway[model_id]

        # Touch cache timestamp to indicate freshness
        self.gateway_manager._cache_timestamp = time.time()
