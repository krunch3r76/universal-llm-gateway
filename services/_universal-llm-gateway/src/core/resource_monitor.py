"""Lightweight resource monitor for gateway state streaming."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from universal_event_bus import Event, EventBus
from universal_logging import get_logger
from universal_protocol.resources import ResourceState
from universal_protocol.state_channel import StateUpdate

from .events.types import (
    INFERENCE_COMPLETED,
    INFERENCE_STARTED,
    MODEL_LOAD_FAILED,
    MODEL_LOADED,
    MODEL_UNLOADED,
    SYSTEM_RESOURCES_UPDATED,
)

logger = get_logger(__name__)


class ResourceMonitor:
    """Track resource state and broadcast updates to state channels."""

    def __init__(self, event_bus: EventBus, gateway_name: str):
        """
        Initialize the resource monitor.

        Args:
            event_bus: Event bus used for lifecycle notifications.
            gateway_name: Logical gateway identifier for state paths.
        """
        self.event_bus = event_bus
        self.gateway_name = gateway_name
        self._state_channels: list[Any] = []
        self._resources: dict[str, ResourceState] = {}
        self._metrics: dict[str, Any] = {
            "active_models": 0,
            "busy_models": 0,
            "total_memory_mb": 0,
            "used_memory_mb": 0,
            "available_vram_mb": None,
            "available_ram_mb": None,
            "total_vram_mb": None,
            "total_ram_mb": None,
        }
        self._version = 0
        self._setup_event_handlers()

    async def get_current_state(self) -> dict[str, ResourceState]:
        """
        Return a shallow copy of the current resource state map.

        Returns:
            Mapping of model_id to ResourceState instances.
        """
        return {model_id: resource for model_id, resource in self._resources.items()}

    async def get_snapshot(self) -> dict[str, Any]:
        """
        Return JSON-serializable snapshot for clients.

        Returns:
            Dict containing metrics and serialized resource data.
        """
        return {
            "metrics": self._metrics.copy(),
            "resources": {
                model_id: resource.to_dict()
                for model_id, resource in self._resources.items()
            },
        }

    async def subscribe_to_updates(self, channel) -> None:
        """Register a state channel to receive updates."""
        self._state_channels.append(channel)
        logger.info(
            "Resource monitor registered state channel for %s", self.gateway_name
        )

    async def unsubscribe_from_updates(self, channel) -> None:
        """Remove a previously registered state channel."""
        if channel in self._state_channels:
            self._state_channels.remove(channel)
            logger.info(
                "Resource monitor unregistered state channel for %s", self.gateway_name
            )

    def _setup_event_handlers(self) -> None:
        """Subscribe to lifecycle events on the event bus."""
        self.event_bus.subscribe_async(MODEL_LOADED, self._on_model_loaded)
        self.event_bus.subscribe_async(MODEL_UNLOADED, self._on_model_unloaded)
        self.event_bus.subscribe_async(MODEL_LOAD_FAILED, self._on_model_load_failed)
        self.event_bus.subscribe_async(INFERENCE_STARTED, self._on_inference_started)
        self.event_bus.subscribe_async(
            INFERENCE_COMPLETED, self._on_inference_completed
        )
        self.event_bus.subscribe_async(
            SYSTEM_RESOURCES_UPDATED, self._on_system_resources_updated
        )

    async def _on_model_loaded(self, event: Event) -> None:
        """Handle model load success."""
        payload = event.payload or {}
        model_id = payload.get("model_id")
        if not model_id:
            logger.warning("ModelLoaded event missing model_id")
            return

        memory_mb = int(
            payload.get("vram_usage_mb", 0) + payload.get("ram_usage_mb", 0)
        )
        process_pid = payload.get("process_pid")

        self._resources[model_id] = ResourceState(
            resource_id=model_id,
            status="idle",
            memory_mb=memory_mb,
            last_updated=datetime.utcnow(),
            process_pid=process_pid,
            metrics={
                "vram_usage_mb": payload.get("vram_usage_mb"),
                "ram_usage_mb": payload.get("ram_usage_mb"),
            },
        )
        self._recalculate_metrics()

        await self._publish_resource_state(model_id)

    async def _on_model_unloaded(self, event: Event) -> None:
        """Handle model unload."""
        payload = event.payload or {}
        model_id = payload.get("model_id")
        if not model_id:
            logger.warning("ModelUnloaded event missing model_id")
            return

        if self._resources.pop(model_id, None):
            self._recalculate_metrics()

        await self._publish_resource_removal(model_id)

    async def _on_model_load_failed(self, event: Event) -> None:
        """Handle failed model load by clearing partial state."""
        payload = event.payload or {}
        model_id = payload.get("model_id")
        if not model_id:
            return

        removed = self._resources.pop(model_id, None)
        if removed:
            self._recalculate_metrics()

        await self._publish_resource_removal(model_id)

    async def _on_inference_started(self, event: Event) -> None:
        """Mark a resource as busy."""
        payload = event.payload or {}
        model_id = payload.get("model_id")
        if not model_id:
            return

        resource = self._resources.get(model_id)
        if not resource:
            return
        resource.status = "busy"
        resource.last_updated = datetime.utcnow()
        # lifecycle events are model-scoped (request tracking via REQUEST_QUEUED)
        self._recalculate_metrics()

        await self._publish_resource_state(model_id)

    async def _on_inference_completed(self, event: Event) -> None:
        """Mark a resource as idle after inference."""
        payload = event.payload or {}
        model_id = payload.get("model_id")
        if not model_id:
            return

        resource = self._resources.get(model_id)
        if not resource:
            return
        resource.status = "idle"
        resource.last_updated = datetime.utcnow()
        # Note: lifecycle events are model-scoped; duration/request_id not available
        self._recalculate_metrics()

        await self._publish_resource_state(model_id)

    async def _on_system_resources_updated(self, event: Event) -> None:
        """Track system-wide metrics."""
        payload = event.payload or {}

        for key in (
            "total_vram_mb",
            "available_vram_mb",
            "total_ram_mb",
            "available_ram_mb",
        ):
            if key in payload:
                self._metrics[key] = payload[key]

        await self._publish_metrics()

    def _recalculate_metrics(self) -> None:
        """Update aggregate metrics from current resource state."""
        active_models = len(self._resources)
        busy_models = sum(
            1 for resource in self._resources.values() if resource.status == "busy"
        )
        total_memory = sum(resource.memory_mb for resource in self._resources.values())

        self._metrics.update(
            {
                "active_models": active_models,
                "busy_models": busy_models,
                "total_memory_mb": total_memory,
                "used_memory_mb": total_memory,
            }
        )

    async def _publish_resource_state(self, model_id: str) -> None:
        """Broadcast updated resource state to listeners."""
        resource = self._resources.get(model_id)
        if not resource:
            return
        value = resource.to_dict()

        await self._broadcast_state_update(
            path=f"gateways.{self.gateway_name}.resources.{model_id}",
            value=value,
        )

    async def _publish_resource_removal(self, model_id: str) -> None:
        """Broadcast resource removal to listeners."""
        await self._broadcast_state_update(
            path=f"gateways.{self.gateway_name}.resources.{model_id}",
            value=None,
        )

    async def _publish_metrics(self) -> None:
        """Broadcast metrics to listeners."""
        metrics_copy = self._metrics.copy()

        await self._broadcast_state_update(
            path=f"gateways.{self.gateway_name}.metrics",
            value=metrics_copy,
        )

    async def _broadcast_state_update(self, path: str, value: Any) -> None:
        """Send update to all registered state channels."""
        self._version += 1
        update = StateUpdate(
            path=path,
            value=value,
            timestamp=time.time(),
            version=self._version,
        )

        for channel in list(self._state_channels):
            try:
                await channel.send_state_update(update)
            except Exception as exc:  # noqa: BLE001
                logger.warning("State channel broadcast failed for %s: %s", path, exc)
