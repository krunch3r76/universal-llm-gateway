"""
Gateway shutdown lifecycle management.

Handles gateway registration with tracker and shutdown event handling.
"""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from universal_logging import get_logger

if TYPE_CHECKING:
    from universal_event_bus import EventBus

    from gateway_client import GatewayConfig

    try:
        from services.universal_stargate.src.core.gateway_tracker import GatewayTracker
        from services.universal_stargate.src.core.shutdown_handler import (
            GatewayShutdownHandler,
        )
    except ImportError:
        from src.core.gateway_tracker import GatewayTracker
        from src.core.shutdown_handler import GatewayShutdownHandler

logger = get_logger(__name__)


def register_gateways_with_tracker(
    gateway_configs: list["GatewayConfig"],
    gateway_tracker: "GatewayTracker",
) -> None:
    """
    Register all configured gateways with the tracker.

    Args:
        gateway_configs: List of gateway configurations
        gateway_tracker: Gateway tracker instance
    """
    for gw_config in gateway_configs:
        # DIAGNOSTIC: Log config details before checking
        logger.info(
            f"register_gateways_with_tracker(): Processing gateway: "
            f"name={gw_config.name}, "
            f"socket_path={gw_config.socket_path}, "
            f"base_url={gw_config.base_url}"
        )

        # Handle Unix socket vs TCP transport
        if gw_config.socket_path:
            # Unix socket transport - use "unix" as host and 0 as port indicator
            host = "unix"
            port = 0
            logger.info(
                f"Registered gateway: {gw_config.name} via Unix socket: {gw_config.socket_path}"
            )
        else:
            # TCP transport - parse host and port from URL
            parsed = urlparse(gw_config.base_url)
            host = parsed.hostname or "localhost"
            port = parsed.port or 9998
            logger.info(f"Registered gateway: {gw_config.name} at {host}:{port}")
        gateway_tracker.register_gateway(gw_config.name, host, port)


def initialize_shutdown_handler(
    gateway_tracker: "GatewayTracker",
    event_bus: "EventBus",
    retry_callback: Callable[[str], Awaitable[None]] | None = None,
) -> "GatewayShutdownHandler":
    """
    Initialize and subscribe gateway shutdown handler.

    Subscribes to:
    - GATEWAY_SHUTDOWN: Explicit shutdown messages from gateways
    - GATEWAY_STATE_CHANGED: Connection state transitions (disconnect detection)

    Args:
        gateway_tracker: Gateway tracker instance
        event_bus: Event bus for subscription
        retry_callback: Optional callback to retry affected requests

    Returns:
        Initialized GatewayShutdownHandler
    """
    try:
        from services.universal_stargate.src.core.shutdown_handler import (
            GATEWAY_SHUTDOWN,
            GatewayShutdownHandler,
        )
    except ImportError:
        from src.core.shutdown_handler import GATEWAY_SHUTDOWN, GatewayShutdownHandler

    from src.scheduling.events import GATEWAY_STATE_CHANGED

    shutdown_handler = GatewayShutdownHandler(
        gateway_tracker=gateway_tracker,
        retry_callback=retry_callback,
    )

    # Subscribe to explicit gateway shutdown events
    event_bus.subscribe_async(
        GATEWAY_SHUTDOWN,
        shutdown_handler.handle_shutdown_event,
    )

    # Subscribe to state change events for disconnect detection
    # This clears stale in-flight slots when gateways become unreachable
    event_bus.subscribe_async(
        GATEWAY_STATE_CHANGED,
        shutdown_handler.handle_state_change,
    )

    logger.info(
        "✅ GatewayShutdownHandler initialized, subscribed to GATEWAY_SHUTDOWN and"
        "GATEWAY_STATE_CHANGED events"
    )

    return shutdown_handler
