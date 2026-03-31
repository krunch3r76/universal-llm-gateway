"""
WebSocket callback implementations for model lifecycle events.

Handles MODEL_LOADED, MODEL_UNLOADED, MODEL_LOAD_FAILED, MODEL_LOADING_STARTED,
and connection state changes.

Phase 6 Enhancement: Emits events to EventBus for BatchModelTracker and cache sync.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from gateways import GatewayInstance

    from ..coordination import GlobalModelLoadCoordinator
    from .handles import LoadWaitHandle, UnloadWaitHandle

logger = get_logger(__name__)


class WebSocketCallbackFactory:
    """
    Factory for creating WebSocket callback handlers.

    Creates closures that capture the necessary context for each gateway,
    including pending handles, coordinator, and event emission.
    """

    def __init__(
        self,
        gateway: GatewayInstance,
        pending_loads: dict[tuple[str, str], LoadWaitHandle],
        pending_unloads: dict[tuple[str, str], UnloadWaitHandle],
        global_load_coordinator: GlobalModelLoadCoordinator | None,
        to_event_key_func,
        event_bus=None,
    ):
        self.gateway = gateway
        self.gateway_name = gateway.config.name
        self.pending_loads = pending_loads
        self.pending_unloads = pending_unloads
        self.global_load_coordinator = global_load_coordinator
        self.to_event_key_func = to_event_key_func
        self.event_bus = event_bus

    def create_on_loading_started(self, original_callback):
        """Create MODEL_LOADING_STARTED callback."""
        gateway_name = self.gateway_name
        coordinator = self.global_load_coordinator

        async def on_model_loading_started(model_id: str) -> None:
            if coordinator:
                coordinator.on_model_loading_started_event(model_id, gateway_name)
                logger.debug(f"🔔 MODEL_LOADING_STARTED: {model_id} on {gateway_name}")

            if original_callback:
                await original_callback(model_id)

        return on_model_loading_started

    def create_on_loaded(self, original_callback):
        """Create MODEL_LOADED callback with EventBus emission."""
        gateway = self.gateway
        gateway_name = self.gateway_name
        pending_loads = self.pending_loads
        coordinator = self.global_load_coordinator
        to_event_key = self.to_event_key_func
        factory = self  # Capture factory for accessing event_bus

        async def on_model_loaded(model_id: str, data: dict) -> None:
            from model_id import ModelId

            from .signals import signal_loaded

            parsed_model_id = ModelId.parse(model_id)  # Parse at boundary
            event_key = to_event_key(parsed_model_id)
            await signal_loaded(
                pending_loads,
                coordinator,
                gateway_name,
                parsed_model_id,
                event_key,
            )

            # Phase 6: Emit to EventBus for cache sync and batch routing
            await _emit_model_loaded_event(
                model_id, gateway, gateway_name, data, factory.event_bus
            )

            if original_callback:
                await original_callback(model_id, data)

        return on_model_loaded

    def create_on_unloaded(self, original_callback):
        """Create MODEL_UNLOADED callback with EventBus emission."""
        gateway = self.gateway
        gateway_name = self.gateway_name
        pending_loads = self.pending_loads
        pending_unloads = self.pending_unloads
        coordinator = self.global_load_coordinator
        to_event_key = self.to_event_key_func
        factory = self  # Capture factory for accessing event_bus

        async def on_model_unloaded(model_id: str) -> None:
            from model_id import ModelId

            from .signals import signal_load_failed, signal_unloaded

            parsed_model_id = ModelId.parse(model_id)  # Parse at boundary
            event_key = to_event_key(parsed_model_id)

            # Signal unload completion for unload waiters
            await signal_unloaded(
                pending_unloads,
                coordinator,
                gateway_name,
                parsed_model_id,
                event_key,
            )

            # Signal load failure for load waiters (model unloaded during load)
            await signal_load_failed(
                pending_loads,
                gateway_name,
                parsed_model_id,
                event_key,
                "Model unloaded during load",
            )

            # Phase 6: Emit to EventBus
            await _emit_model_unloaded_event(
                model_id, gateway, gateway_name, factory.event_bus
            )

            if original_callback:
                await original_callback(model_id)

        return on_model_unloaded

    def create_on_load_failed(self, original_callback):
        """Create MODEL_LOAD_FAILED callback with EventBus emission."""
        gateway = self.gateway
        gateway_name = self.gateway_name
        pending_loads = self.pending_loads
        coordinator = self.global_load_coordinator
        to_event_key = self.to_event_key_func
        factory = self  # Capture factory for accessing event_bus

        async def on_model_load_failed(model_id: str, error_message: str) -> None:
            from model_id import ModelId

            from .signals import signal_load_failed

            parsed_model_id = ModelId.parse(model_id)  # Parse at boundary
            event_key = to_event_key(parsed_model_id)
            await signal_load_failed(
                pending_loads,
                gateway_name,
                parsed_model_id,
                event_key,
                error_message,
            )

            # Notify global coordinator
            if coordinator:
                coordinator.on_model_load_failed_event(
                    model_id, gateway_name, error_message
                )
                logger.info(
                    f"🔔 MODEL_LOAD_FAILED via WebSocket: {model_id} on "
                    f"{gateway_name} - {error_message}"
                )

            # Phase 6: Emit to EventBus
            await _emit_model_loading_failed_event(
                model_id, gateway, gateway_name, error_message, factory.event_bus
            )

            if original_callback:
                await original_callback(model_id, error_message)

        return on_model_load_failed

    def create_on_connected(self, original_callback):
        """Create connection/reconnection callback."""
        gateway_name = self.gateway_name
        coordinator = self.global_load_coordinator

        async def on_connected() -> None:
            # Clear coordinator-verified state on reconnection
            # (we may have missed events during disconnection)
            if coordinator:
                coordinator.clear_verified_state_for_gateway(gateway_name)
                logger.debug(
                    "🔄 WebSocket reconnected for %s, "
                    "cleared coordinator-verified state",
                    gateway_name,
                )

            if original_callback:
                await original_callback()

        return on_connected


async def _emit_model_loaded_event(
    model_id: str,
    gateway: GatewayInstance,
    gateway_name: str,
    data: dict,
    event_bus,
) -> None:
    """Emit MODEL_LOADED event to EventBus."""
    if not event_bus:
        return

    try:
        from model_id import ModelId

        from ..model_event_emitter import emit_model_loaded

        await emit_model_loaded(
            event_bus=event_bus,
            model_id=ModelId.parse(model_id),
            gateway_url=gateway.config.base_url,
            gateway_name=gateway_name,
            vram_mb=data.get("vram_mb", 0),
            ram_mb=data.get("ram_mb", 0),
        )
    except Exception as e:
        logger.debug(f"Failed to emit MODEL_LOADED event: {e}")


async def _emit_model_unloaded_event(
    model_id: str,
    gateway: GatewayInstance,
    gateway_name: str,
    event_bus,
) -> None:
    """Emit MODEL_UNLOADED event to EventBus."""
    if not event_bus:
        return

    try:
        from model_id import ModelId

        from ..model_event_emitter import emit_model_unloaded

        await emit_model_unloaded(
            event_bus=event_bus,
            model_id=ModelId.parse(model_id),
            gateway_url=gateway.config.base_url,
            gateway_name=gateway_name,
        )
    except Exception as e:
        logger.debug(f"Failed to emit MODEL_UNLOADED event: {e}")


async def _emit_model_loading_failed_event(
    model_id: str,
    gateway: GatewayInstance,
    gateway_name: str,
    error_message: str,
    event_bus,
) -> None:
    """Emit MODEL_LOAD_FAILED event to EventBus."""
    if not event_bus:
        return

    try:
        from model_id import ModelId

        from ..model_event_emitter import emit_model_loading_failed

        await emit_model_loading_failed(
            event_bus=event_bus,
            model_id=ModelId.parse(model_id),
            gateway_url=gateway.config.base_url,
            gateway_name=gateway_name,
            error_message=error_message,
        )
    except Exception as e:
        logger.debug(f"Failed to emit MODEL_LOAD_FAILED event: {e}")
