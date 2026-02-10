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

    async def verify_gateway_resources(
        self, gateway, queued_request
    ) -> VerificationResult:
        """
        Verify gateway has sufficient resources for the request.

        This is a lightweight check that happens after the router has already
        chosen a gateway using its heuristics. We validate the choice is
        still valid given current conditions.

        Phase 4: Event-driven verification caching (invalidated by
        GATEWAY_RESOURCE_UPDATE).

        Args:
            gateway: Gateway instance to verify
            queued_request: Request requiring resources

        Returns:
            VerificationResult indicating PASS, FAIL, or ERROR
        """
        gateway_url = gateway.config.base_url
        cache_key = (gateway_url, queued_request.model_id)

        # Check verification cache - always fresh (invalidated by events)
        if cache_key in self._recent_verifications:
            passed = self._recent_verifications[cache_key]
            self.logger.debug(
                f"Using cached verification for {queued_request.model_id} on "
                f"{gateway.config.name} (result: {'PASS' if passed else 'FAIL'})"
            )
            self.verifications_cached += 1
            return VerificationResult.PASS if passed else VerificationResult.FAIL

        try:
            # Get current gateway resource status
            # For queue verification, cached status is sufficient (resource changes
            # trigger cache invalidation via GATEWAY_RESOURCE_UPDATE events)
            status = gateway.client.get_resource_status()
            if not status:
                # WebSocket not connected = gateway unavailable
                # Can't verify - fail open (trust router)
                self.logger.debug(
                    f"Unable to get status for gateway {gateway.config.name}, "
                    f"trusting router decision"
                )
                self.verifications_errors += 1
                return VerificationResult.ERROR

            # If model is already loaded, we're good (cache this result)
            if queued_request.model_id in status.loaded_models:
                self.logger.debug(
                    f"Model {queued_request.model_id} already loaded on "
                    f"{gateway.config.name}, verification passed"
                )
                self._recent_verifications[cache_key] = True
                self.verifications_passed += 1
                return VerificationResult.PASS

            # Get model requirements from gateway manager
            model_config = await self._fetch_configuration(queued_request.model_id)

            if not model_config:
                # Can't verify - fail open (trust router)
                self.logger.debug(
                    f"No configuration for {queued_request.model_id}, trusting router"
                )
                self.verifications_errors += 1
                return VerificationResult.ERROR

            # Check: does gateway have the resources right now?
            has_ram = status.available_ram_mb >= model_config.ram_usage
            has_vram = status.available_vram_mb >= model_config.vram_usage

            if not (has_ram and has_vram):
                self.logger.info(
                    f"Gateway {gateway.config.name} lacks resources for "
                    f"{queued_request.model_id}: "
                    f"RAM {status.available_ram_mb}/{model_config.ram_usage}MB, "
                    f"VRAM {status.available_vram_mb}/{model_config.vram_usage}MB"
                )
                # Cache the failure (event-driven invalidation)
                self._recent_verifications[cache_key] = False
                self.verifications_failed += 1
                return VerificationResult.FAIL

            self.logger.debug(
                f"Resource verification passed for {queued_request.model_id} on "
                f"{gateway.config.name}: "
                f"RAM {status.available_ram_mb}/{model_config.ram_usage}MB, "
                f"VRAM {status.available_vram_mb}/{model_config.vram_usage}MB"
            )
            # Cache the success (event-driven invalidation)
            self._recent_verifications[cache_key] = True
            self.verifications_passed += 1
            return VerificationResult.PASS

        except Exception as e:
            # Don't block on verification errors - trust the router
            self.logger.warning(
                f"Resource verification error for {queued_request.model_id} on "
                f"{gateway.config.name if gateway else 'unknown'}: {e}, "
                f"proceeding anyway"
            )
            self.verifications_errors += 1
            return VerificationResult.ERROR

    def get_metrics(self) -> dict[str, int]:
        """Get verification metrics including caching stats."""
        total_checks = (
            self.verifications_passed
            + self.verifications_failed
            + self.verifications_errors
            + self.verifications_cached
        )

        return {
            "verifications_passed": self.verifications_passed,
            "verifications_failed": self.verifications_failed,
            "verifications_errors": self.verifications_errors,
            "verifications_cached": self.verifications_cached,
            "total_verifications": total_checks,
            "cache_hit_rate": (
                self.verifications_cached / total_checks if total_checks > 0 else 0.0
            ),
            "configuration_cache_size": len(self._configuration_cache),
            "verification_cache_size": len(self._recent_verifications),
        }

    def clear_cache_entries(self):
        """
        Clear all cache entries.

        Phase 4: Caches are now event-driven (no TTL-based expiration).
        This method is kept for manual cache clearing if needed (e.g., debugging).
        """
        configuration_count = len(self._configuration_cache)
        verification_count = len(self._recent_verifications)

        self._configuration_cache.clear()
        self._recent_verifications.clear()

        if configuration_count > 0 or verification_count > 0:
            self.logger.debug(
                f"Cleared {configuration_count} configuration entries, "
                f"{verification_count} verification entries"
            )
