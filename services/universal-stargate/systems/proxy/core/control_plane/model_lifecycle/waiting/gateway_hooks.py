"""
Gateway registration for WebSocket event hooks.

Registers gateways to receive model lifecycle events via WebSocket callbacks.
Callback implementations are in websocket_callbacks.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from universal_logging import get_logger

from .handles import LoadWaitHandle, UnloadWaitHandle, _GatewayWsClient
from .websocket_callbacks import WebSocketCallbackFactory

if TYPE_CHECKING:
    from gateways import GatewayInstance

    from ..coordination import GlobalModelLoadCoordinator

logger = get_logger(__name__)


def register_gateway(
    gateway: GatewayInstance,
    registered_gateways: set[str],
    ws_clients_by_gateway: dict[str, _GatewayWsClient],
    gateway_instances: dict[str, GatewayInstance],
    pending_loads: dict[tuple[str, str], LoadWaitHandle],
    pending_unloads: dict[tuple[str, str], UnloadWaitHandle],
    global_load_coordinator: GlobalModelLoadCoordinator | None,
    to_event_key_func,
    event_bus=None,
) -> None:
    """
    Register a gateway to receive load/unload events from its WebSocket client.

    Hooks into model lifecycle callbacks for real-time notification of state
    transitions. Phase 6: Also emits events to EventBus for BatchModelTracker.

    Args:
        gateway: Gateway instance to register
        registered_gateways: Set of already registered gateway names
        ws_clients_by_gateway: Dict mapping gateway name to WS client
        gateway_instances: Dict mapping gateway name to gateway instance
        pending_loads: Dict of pending load handles
        pending_unloads: Dict of pending unload handles
        global_load_coordinator: Global coordinator for state updates
        to_event_key_func: Function to normalize model IDs
        event_bus: EventBus instance for emitting events
    """
    gateway_name = gateway.config.name
    if gateway_name in registered_gateways:
        return

    ws_client = gateway.client._ws_client
    ws_clients_by_gateway[gateway_name] = cast(_GatewayWsClient, ws_client)
    gateway_instances[gateway_name] = gateway

    # Create callback factory with all required context
    factory = WebSocketCallbackFactory(
        gateway=gateway,
        pending_loads=pending_loads,
        pending_unloads=pending_unloads,
        global_load_coordinator=global_load_coordinator,
        to_event_key_func=to_event_key_func,
        event_bus=event_bus,
    )

    # Wire up all callbacks
    ws_client._on_model_loading_started = factory.create_on_loading_started(
        ws_client._on_model_loading_started
    )
    ws_client._on_model_loaded = factory.create_on_loaded(ws_client._on_model_loaded)
    ws_client._on_model_unloaded = factory.create_on_unloaded(
        ws_client._on_model_unloaded
    )
    ws_client._on_model_load_failed = factory.create_on_load_failed(
        ws_client._on_model_load_failed
    )
    ws_client._on_connected = factory.create_on_connected(ws_client._on_connected)

    registered_gateways.add(gateway_name)
    logger.debug(f"Registered state waiter for gateway: {gateway_name}")
