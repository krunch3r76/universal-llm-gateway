"""Cached INIT data provider to avoid sync I/O on WebSocket connect."""

import asyncio
import os
import socket
from typing import Any

from universal_logging import get_logger

from src import __version__
from src.core.catalog import get_catalog_loader
from src.core.events import Event, EventBus
from src.core.events.types import CATALOG_RELOADED
from src.core.gateway_config import GatewayConfig
from src.core.model_registry import ModelRegistry
from src.core.resources.hardware import get_ram_info, get_vram_info

logger = get_logger(__name__)


class InitDataCache:
    """
    Caches INIT message data to avoid blocking I/O on WebSocket connect.

    Data is computed once at startup and refreshed on CATALOG_RELOADED events.
    All file I/O happens asynchronously off the WebSocket connect path.

    Subscribes to CATALOG_RELOADED events to automatically refresh cached data
    when catalog changes, ensuring all new connections get current model lists.
    """

    def __init__(
        self,
        model_registry: ModelRegistry,
        worker_controller: Any,
        event_bus: EventBus | None = None,
        *,
        gateway_config: GatewayConfig,
    ):
        self._model_registry = model_registry
        self._worker_controller = worker_controller
        self._event_bus = event_bus
        self._gateway_config = gateway_config
        self._cached_data: dict[str, Any] | None = None
        self._subscribed = False

        if self._event_bus:
            self._event_bus.subscribe_async(
                CATALOG_RELOADED, self._handle_catalog_reload
            )
            self._subscribed = True
            logger.debug("InitDataCache subscribed to CATALOG_RELOADED events")

    async def refresh(self) -> None:
        """Refresh cached INIT data asynchronously."""
        # Build new cache data (all I/O happens here)
        loop = asyncio.get_running_loop()
        catalog_summary = await loop.run_in_executor(None, self._get_catalog_summary)

        # Build complete new cache
        model_ids = self._get_all_model_ids()
        new_cache = {
            "version": __version__,
            "gateway_name": os.environ.get("GATEWAY_NAME", socket.gethostname()),
            "models": model_ids,
            "catalog": catalog_summary,
        }

        # Atomic swap (dict assignment is atomic in Python)
        self._cached_data = new_cache
        logger.debug(f"INIT cache refreshed with {len(model_ids)} model(s)")

    async def _handle_catalog_reload(self, event: Event) -> None:
        """
        Handle CATALOG_RELOADED event by refreshing cache.

        Ensures all new WebSocket connections get fresh model lists after
        catalog changes (model additions, removals, metadata updates).
        """
        # Skip if cleanup() was called
        # (handlers can't be unsubscribed in EventBus v0.2.0+)
        if not self._subscribed:
            return

        try:
            await self.refresh()
            reason = event.payload.get("reason", "unknown")
            logger.info(
                f"✅ INIT cache refreshed after catalog reload (reason: {reason})"
            )
        except Exception as e:
            logger.error(
                f"Failed to refresh INIT cache on catalog reload: {e}", exc_info=True
            )

    def cleanup(self) -> None:
        """Cleanup event subscriptions.

        Note: Handlers remain subscribed to EventBus (by design).
        Sets flag that handler checks to skip processing.
        """
        if self._event_bus and self._subscribed:
            # Set flag - handler checks this to skip processing
            self._subscribed = False
            logger.debug("InitDataCache cleanup complete")

    async def get_init_data(self) -> dict[str, Any]:
        """
        Get cached INIT data with current runtime state.

        Returns data immediately without blocking I/O. Runtime state
        (loaded_models, resources) is computed fresh since it changes frequently.

        Returns:
            Dictionary with version, gateway_name, models, loaded_models,
            catalog, resources
        """
        if self._cached_data is None:
            logger.warning("INIT cache not initialized, using empty data")
            return self._get_fallback_data()

        # Merge cached static data with fresh runtime state
        return {
            **self._cached_data,
            "loaded_models": await self._get_loaded_models(),
            "resources": self.get_resources(),
        }

    def get_resources(self) -> dict[str, Any]:
        """Get current resource status."""
        return self._get_resources()

    def _get_all_model_ids(self) -> list[str]:
        """
        Get all available synthetic model IDs with file validation.

        Uses ModelRegistry.get_available_synthetic_model_ids() to ensure
        consistent filtering behavior with /v1/models endpoint.

        Only returns models that are:
        - Enabled in the catalog
        - Have accessible file paths (file validation passed)

        This prevents advertising "phantom" models that can't be loaded.
        """
        model_ids = self._model_registry.get_available_synthetic_model_ids(
            enabled_only=True, available_only=True
        )
        return model_ids

    def _get_catalog_summary(self) -> dict[str, Any]:
        """Get catalog summary (sync operation, run in executor).

        Note: Transformations are NOT included - they belong to Stargate.
        Gateway is a pure passthrough and does not handle request modifications.

        Includes model_resources for federated routing resource calculations.
        """
        catalog_loader = get_catalog_loader()
        catalog = catalog_loader.load()

        # Extract activated contexts from model metadata
        activated_contexts: dict[str, dict[str, Any]] = {}
        for model_id, model_data in catalog.get("models", {}).items():
            metadata = model_data.get("metadata", {})
            gpu = metadata.get("activated_gpu_contexts")
            cpu = metadata.get("activated_cpu_contexts")
            if gpu is not None or cpu is not None:
                activated_contexts[model_id] = {}
                if gpu is not None:
                    activated_contexts[model_id]["gpu"] = gpu
                if cpu is not None:
                    activated_contexts[model_id]["cpu"] = cpu

        # Extract model resource requirements for federated routing
        # Uses synthetic model IDs (e.g., "model-32768") for context-based sizing
        model_resources: dict[str, dict[str, int | str]] = {}
        model_ids = self._model_registry.get_available_synthetic_model_ids(
            enabled_only=True, available_only=True
        )
        for model_id in model_ids:
            resources = self._model_registry.get_model_resources(model_id)
            if resources:
                model_config = self._model_registry.get_model_config(model_id)
                info = model_config.get("info", {}) if model_config else {}
                # Default: messages (OpenAI standard format)
                input_schema = info.get("input_schema", "messages")

                model_resources[model_id] = {
                    "vram_usage": resources.get("vram_mb", 0),
                    "ram_usage": resources.get("ram_mb", 0),
                    "input_schema": input_schema,
                }
                # Concurrency: llama parallel_slots, vLLM max_num_seqs
                loader_config = self._model_registry.get_model_loader_config(model_id)
                max_concurrent_requests = 1
                if loader_config is not None:
                    max_concurrent_requests = loader_config.get("parallel_slots", 1)
                else:
                    logger.warning(
                        "Unknown engine for %s: no loader_config, max_concurrent=1",
                        model_id,
                    )
                if max_concurrent_requests < 1:
                    max_concurrent_requests = 1
                model_resources[model_id]["max_concurrent_requests"] = (
                    max_concurrent_requests
                )

                # Context capacity metadata: total context, slots, effective
                # per-slot context (KV cache is split across parallel slots)
                total_context = self._model_registry.get_model_max_tokens(model_id)
                if total_context:
                    model_resources[model_id]["context_length"] = total_context
                    model_resources[model_id]["parallel_slots"] = (
                        max_concurrent_requests
                    )
                    model_resources[model_id]["effective_context_per_slot"] = (
                        total_context // max_concurrent_requests
                    )

        # DEBUG: Log telemetry data being sent to Stargate
        logger.debug(
            f"📊 [TELEMETRY] Catalog summary for GATEWAY_SNAPSHOT: "
            f"{len(model_resources)} model resource entries, "
            f"{len(activated_contexts)} activated contexts"
        )
        if model_resources:
            sample_models = list(model_resources.items())[:3]
            logger.debug(
                f"📊 [TELEMETRY] Sample model_resources (first 3): {sample_models}"
            )

        return {
            "schema_version": catalog.get("schema_version", 2),
            "activated_contexts": activated_contexts,
            "model_resources": model_resources,
        }

    async def _get_loaded_models(self) -> list[str]:
        """Get currently loaded model IDs using controller method."""
        try:
            if self._worker_controller:
                # Get ALL loaded models from controller
                return self._worker_controller.get_active_models()
            return []
        except Exception as e:
            logger.warning(f"Failed to get loaded models: {e}")
            return []

    def _get_resources(self) -> dict[str, Any]:
        """Get current resource status using conservative estimates.

        Uses max(catalog_estimated_usage, actual_hardware_usage) to ensure
        Stargate never over-estimates available resources.

        Invariant: available = total - max(catalog_estimate, hardware_used)
        """
        vram_info = get_vram_info()
        ram_info = get_ram_info()

        total_vram = vram_info["total_vram_mb"]
        total_ram = ram_info["total_ram_mb"]
        hardware_available_vram = vram_info["available_vram_mb"]
        hardware_available_ram = ram_info["available_ram_mb"]

        # Calculate hardware-measured used resources
        hardware_used_vram = total_vram - hardware_available_vram
        hardware_used_ram = total_ram - hardware_available_ram

        # Calculate catalog-estimated used resources from loaded models
        catalog_used_vram = 0
        catalog_used_ram = 0

        if self._worker_controller:
            try:
                loaded_models = self._worker_controller.get_active_models()
                for model_id in loaded_models:
                    # Get catalog estimates for this model
                    config = self._model_registry.get_model_loader_config(model_id)
                    if config:
                        catalog_used_vram += config.get("vram_usage", 0) or 0
                        catalog_used_ram += config.get("ram_usage", 0) or 0
            except Exception:
                pass  # Fall back to hardware-only if catalog lookup fails

        # Use conservative estimate: max of catalog vs hardware
        conservative_used_vram = max(catalog_used_vram, hardware_used_vram)
        conservative_used_ram = max(catalog_used_ram, hardware_used_ram)

        # Available = total - conservative used
        available_vram = max(0, total_vram - conservative_used_vram)
        available_ram = max(0, total_ram - conservative_used_ram)

        return {
            "total_ram_mb": total_ram,
            "available_ram_mb": available_ram,
            "total_vram_mb": total_vram,
            "available_vram_mb": available_vram,
        }

    def _get_fallback_data(self) -> dict[str, Any]:
        """Get fallback data if cache not initialized."""
        return {
            "version": __version__,
            "gateway_name": os.environ.get("GATEWAY_NAME", socket.gethostname()),
            "models": [],
            "loaded_models": [],
            "catalog": {
                "schema_version": 2,
                "activated_contexts": {},
                "model_resources": {},
            },
            "resources": {
                "total_ram_mb": 0,
                "available_ram_mb": 0,
                "total_vram_mb": 0,
                "available_vram_mb": 0,
            },
        }
