from enum import Enum
from typing import Any

from model_id import ModelId
from universal_logging import get_logger

from src.scheduling.events import (
    GATEWAY_RESOURCE_UPDATE,
    MODEL_LOADED,
    MODEL_UNLOADED,
)

logger = get_logger(__name__)


class VerificationResult(Enum):
    """Result of gateway resource verification."""

    PASS = "pass"  # Gateway has sufficient resources
    FAIL = "fail"  # Gateway lacks resources
    ERROR = "error"  # Verification error, fail-safe to trust router


class ResourceVerifier:
    """
    Encapsulates gateway resource verification logic with event-driven caching.

    Uses typed ModelMetadata configuration for resource requirements.
    Caches invalidated by MODEL_LOADED/MODEL_UNLOADED/GATEWAY_RESOURCE_UPDATE events.

    Phase 4 enhancements:
    - Event-driven cache invalidation (replaces TTL-based expiration)
    - Configuration cache invalidated on MODEL_LOADED/MODEL_UNLOADED events
    - Verification cache invalidated on GATEWAY_RESOURCE_UPDATE events
    """

    def __init__(
        self,
        router,
        logger,
        event_bus=None,
    ):
        self.router = router
        self.logger = logger
        self.event_bus = event_bus

        # Verification metrics
        self.verifications_passed = 0
        self.verifications_failed = 0
        self.verifications_errors = 0
        self.verifications_cached = 0

        # Event-driven caching (no TTL checks - invalidated by events)
        self._configuration_cache: dict[ModelId, Any] = {}  # ModelId -> configuration

        self._recent_verifications: dict[
            tuple[str, str], bool
        ] = {}  # (gateway_url, model_id) -> passed

        # Subscribe to cache invalidation events
        if self.event_bus:
            self._setup_event_subscriptions()

    def _setup_event_subscriptions(self):
        """Subscribe to WebSocket events for cache invalidation."""
        try:
            self.event_bus.subscribe_async(MODEL_LOADED, self._on_model_loaded)
            self.event_bus.subscribe_async(MODEL_UNLOADED, self._on_model_unloaded)
            self.event_bus.subscribe_async(
                GATEWAY_RESOURCE_UPDATE, self._on_resource_update
            )
            self.logger.info(
                "✅ ResourceVerifier subscribed to cache invalidation events"
            )
        except Exception as e:
            self.logger.warning(f"⚠️ Failed to setup cache invalidation events: {e}")

    async def _on_model_loaded(self, event) -> None:
        """Invalidate configuration cache when model loads."""
        model_id_str = (
            event.payload.get("model_id")
            if hasattr(event, "payload")
            else event.get("model_id")
        )
        if model_id_str:
            # Parse to ModelId for cache key lookup (cache uses ModelId keys)
            try:
                parsed = ModelId.parse(model_id_str)
                if parsed in self._configuration_cache:
                    del self._configuration_cache[parsed]
                    self.logger.debug(
                        f"📢 Invalidated configuration cache for {model_id_str} (MODEL_LOADED)"
                    )
            except ValueError:
                # Invalid model ID format, skip cache invalidation
                pass

    async def _on_model_unloaded(self, event) -> None:
        """Invalidate configuration cache when model unloads."""
        model_id_str = (
            event.payload.get("model_id")
            if hasattr(event, "payload")
            else event.get("model_id")
        )
        if model_id_str:
            # Parse to ModelId for cache key lookup (cache uses ModelId keys)
            try:
                parsed = ModelId.parse(model_id_str)
                if parsed in self._configuration_cache:
                    del self._configuration_cache[parsed]
                    self.logger.debug(
                        f"📢 Invalidated configuration cache for {model_id_str} (MODEL_UNLOADED)"
                    )
            except ValueError:
                # Invalid model ID format, skip cache invalidation
                pass

    async def _on_resource_update(self, event) -> None:
        """Invalidate verification cache when resources change."""
        # Extract resource info for logging
        payload = event.payload if hasattr(event, "payload") else event
        gateway_url = (
            payload.get("url", "unknown") if isinstance(payload, dict) else "unknown"
        )
        available_vram = (
            payload.get("available_vram_mb", 0) if isinstance(payload, dict) else 0
        )
        available_ram = (
            payload.get("available_ram_mb", 0) if isinstance(payload, dict) else 0
        )

        # Clear all verifications - resource state has changed
        if self._recent_verifications:
            count = len(self._recent_verifications)
            self._recent_verifications.clear()
            self.logger.info(
                f"📢 GATEWAY_RESOURCE_UPDATE received from {gateway_url}: "
                f"Invalidated {count} verification cache entries, "
                f"available_vram={available_vram}MB, available_ram={available_ram}MB"
            )
        else:
            self.logger.info(
                f"📢 GATEWAY_RESOURCE_UPDATE received from {gateway_url}: "
                f"available_vram={available_vram}MB, available_ram={available_ram}MB "
                f"(no cache to invalidate)"
            )

    async def _fetch_configuration(self, model_id: str):
        """
        Retrieve model configuration from gateway manager with event-driven caching.

        Cache invalidated by MODEL_LOADED/MODEL_UNLOADED events.
        Cache is always fresh - no TTL checks needed.

        Args:
            model_id: Model identifier (string, will be parsed to ModelId)

        Returns:
            Model configuration or None if unavailable
        """
        if not self.router or not hasattr(self.router, "gateway_manager"):
            return None

        # Parse to ModelId for type-safe API call
        from model_id import ModelId

        parsed_model_id = ModelId.parse(model_id)

        # Check cache - always fresh (invalidated by events)
        if parsed_model_id in self._configuration_cache:
            config = self._configuration_cache[parsed_model_id]
            self.logger.debug(f"Using cached configuration for {model_id}")
            return config

        try:
            # Use gateway_manager for multi-gateway fallback
            # (fast-fails on unhealthy gateways)
            config = await self.router.gateway_manager.fetch_model_configuration(
                parsed_model_id
            )

            # Cache the result (no timestamp - event-driven invalidation)
            if config:
                self._configuration_cache[parsed_model_id] = config

            return config
        except Exception as e:
            self.logger.debug(f"Error getting model configuration for {model_id}: {e}")
            return None

