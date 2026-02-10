"""WebSocket lifecycle management for Stargate control plane."""

from fastapi import FastAPI
from universal_logging import get_logger

logger = get_logger(__name__)


async def initialize_websocket_forwarder(app: FastAPI) -> None:
    """
    Initialize WebSocket event forwarder for Stargate control plane.

    Sets up:
    - Connection manager for WebSocket clients
    - Event forwarder to push EventBus events to connected clients
    - Cached INIT data provider to avoid sync I/O on connect
    - Telemetry heartbeat publisher for idle period health signaling

    Args:
        app: FastAPI application with event_bus in state
    """
    try:
        import os
        import socket

        from ..core.websocket import WebSocketEventForwarder, get_connection_manager
        from ..core.websocket.init_cache import InitDataCache

        event_bus = app.state.event_bus
        model_registry = app.state.model_registry
        worker_controller = app.state.worker_controller
        gateway_config = app.state.gateway_config

        # Initialize cached INIT data provider with event bus subscription
        init_cache = InitDataCache(
            model_registry,
            worker_controller,
            event_bus,
            gateway_config=gateway_config,
        )
        await init_cache.refresh()  # Initial population
        app.state.init_cache = init_cache

        # Initialize connection manager and event forwarder
        ws_connection_manager = get_connection_manager()

        # Initialize heartbeat publisher with gateway_id from config
        gateway_id = os.getenv("GATEWAY_NAME", socket.gethostname())
        ws_connection_manager.initialize_heartbeat(
            gateway_id=gateway_id,
            interval_seconds=30.0,  # Half of unreachable threshold (60s)
        )

        # Start heartbeat publisher background task
        await ws_connection_manager.start_heartbeat_publisher()

        # Initialize resource telemetry publisher
        resource_tracker = app.state.resource_tracker
        ws_connection_manager.initialize_resource_telemetry(
            resource_tracker=resource_tracker,
            # 5s ensures telemetry stays fresh within 2000ms threshold
            interval_seconds=5.0,
        )

        # Start resource telemetry publisher background task
        await ws_connection_manager.start_resource_telemetry_publisher()

        ws_event_forwarder = WebSocketEventForwarder(
            event_bus, ws_connection_manager, init_cache
        )
        ws_event_forwarder.start()

        app.state.ws_event_forwarder = ws_event_forwarder
        app.state.ws_connection_manager = ws_connection_manager

        logger.info("WebSocket event forwarder started for Stargate control plane")
    except Exception as e:
        logger.error(f"Failed to start WebSocket event forwarder: {e}", exc_info=True)
        logger.warning(f"Failed to start WebSocket event forwarder: {e}")
        raise


async def shutdown_websocket_forwarder(app: FastAPI) -> None:
    """
    Shutdown WebSocket event forwarder for Stargate control plane.

    Cleans up:
    - Unsubscribes event forwarder from EventBus
    - Closes all WebSocket connections gracefully
    - Cancels ping tasks
    - Cleans up INIT cache subscriptions

    Args:
        app: FastAPI application with ws_event_forwarder in state
    """
    try:
        if hasattr(app.state, "ws_event_forwarder") and app.state.ws_event_forwarder:
            app.state.ws_event_forwarder.stop()
        if (
            hasattr(app.state, "ws_connection_manager")
            and app.state.ws_connection_manager
        ):
            await app.state.ws_connection_manager.shutdown()
        if hasattr(app.state, "init_cache") and app.state.init_cache:
            app.state.init_cache.cleanup()
        logger.info("WebSocket event forwarder shutdown completed")
    except Exception as e:
        logger.warning(f"Error shutting down WebSocket event forwarder: {e}")
