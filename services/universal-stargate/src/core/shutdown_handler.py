"""
Handler for gateway disconnect and shutdown events.

Clears in-flight request tracking when gateways disconnect or shut down.
This prevents stale slot reservations from blocking new requests.

Subscribes to:
- GATEWAY_SHUTDOWN: Explicit shutdown message from gateway
- GATEWAY_STATE_CHANGED: Connection state transitions (for disconnect detection)
"""

import time
from collections.abc import Awaitable, Callable

from universal_logging import get_logger

from .gateway_tracker import GatewayTracker

logger = get_logger(__name__)

# Event signal constants (must match gateway's definitions)
GATEWAY_SHUTDOWN = "GatewayShutdown"
GATEWAY_DRAINING = "GatewayDraining"

# Connectivity state value (from src.scheduling.gateway_state)
CONNECTIVITY_UNREACHABLE = "unreachable"


class GatewayShutdownHandler:
    """
    Handles gateway disconnect and shutdown events.

    Clears in-flight request tracking on:
    1. Explicit GATEWAY_SHUTDOWN events (graceful shutdown)
    2. GATEWAY_STATE_CHANGED events with connectivity=unreachable (connection lost)

    This ensures stale slot reservations are cleared when gateways
    restart or disconnect, preventing 503 errors from phantom capacity.
    """

    def __init__(
        self,
        gateway_tracker: GatewayTracker,
        retry_callback: Callable[[str], Awaitable[None]] | None = None,
    ):
        """
        Initialize shutdown handler.

        Args:
            gateway_tracker: GatewayTracker instance
            retry_callback: Async callback to retry a request by ID
        """
        self._gateway_tracker: GatewayTracker = gateway_tracker
        self._retry_callback: Callable[[str], Awaitable[None]] | None = retry_callback
        self._shutdown_count: int = 0
        self._disconnect_count: int = 0

    async def handle_shutdown_event(self, event) -> None:
        """
        Handle a GATEWAY_SHUTDOWN event.

        Args:
            event: Event with payload {gateway_id, reason, timestamp}
        """
        payload = event.payload
        gateway_id = payload.get("gateway_id")
        reason = payload.get("reason", "unknown")
        timestamp = payload.get("timestamp", 0)

        if not gateway_id:
            logger.warning("GATEWAY_SHUTDOWN event missing gateway_id")
            return

        self._shutdown_count += 1
        logger.info(
            f"Received GATEWAY_SHUTDOWN for {gateway_id} "
            f"(reason={reason}, shutdown #{self._shutdown_count})"
        )

        # Mark gateway as unavailable and get affected requests
        affected_requests = self._gateway_tracker.mark_shutdown(gateway_id, timestamp)

        # Trigger retry for affected requests
        if affected_requests and self._retry_callback:
            logger.info(f"Retrying {len(affected_requests)} requests from {gateway_id}")
            for request_id in affected_requests:
                try:
                    await self._retry_callback(request_id)
                except Exception as e:
                    logger.error(f"Failed to retry request {request_id}: {e}")

    async def handle_draining_event(self, event) -> None:
        """
        Handle a GATEWAY_DRAINING event.

        Marks gateway as draining (unavailable for NEW requests)
        but does NOT trigger retry of in-flight requests.

        Args:
            event: Event with payload {gateway_id, reason, timeout, timestamp}
        """
        payload = event.payload
        gateway_id = payload.get("gateway_id")
        timeout = payload.get("timeout", 30)
        timestamp = payload.get("timestamp")

        if not gateway_id:
            logger.warning("GATEWAY_DRAINING event missing gateway_id")
            return

        logger.info(
            f"Gateway {gateway_id} is draining (timeout={timeout}s), "
            f"routing new requests elsewhere"
        )

        # Mark as draining (unavailable for new requests only)
        self._gateway_tracker.mark_draining(gateway_id, timestamp)

    async def handle_state_change(self, event) -> None:
        """
        Handle GATEWAY_STATE_CHANGED events.

        When connectivity becomes 'unreachable', clear in-flight request
        tracking to prevent stale slot reservations.

        Args:
            event: Event with payload {gateway_name, connectivity, ...}
        """
        payload = event.payload
        gateway_name = payload.get("gateway_name")
        connectivity = payload.get("connectivity")
        previous_connectivity = payload.get("previous_connectivity")

        if not gateway_name:
            logger.warning("GATEWAY_STATE_CHANGED event missing gateway_name")
            return

        # Only act on transitions TO unreachable (disconnect events)
        if connectivity != CONNECTIVITY_UNREACHABLE:
            return

        # Avoid duplicate cleanup if already unreachable
        if previous_connectivity == CONNECTIVITY_UNREACHABLE:
            return

        self._disconnect_count += 1
        logger.info(
            f"🔌 Gateway {gateway_name} disconnected "
            f"(disconnect #{self._disconnect_count}), clearing in-flight requests"
        )

        # Clear in-flight tracking for this gateway
        timestamp = time.time()
        affected_requests = self._gateway_tracker.mark_shutdown(gateway_name, timestamp)

        if affected_requests:
            logger.info(
                f"Cleared {len(affected_requests)} stale in-flight slots "
                f"for {gateway_name}"
            )

            # Trigger retry for affected requests if callback provided
            if self._retry_callback:
                for request_id in affected_requests:
                    try:
                        await self._retry_callback(request_id)
                    except Exception as e:
                        logger.error(f"Failed to retry request {request_id}: {e}")

    def get_shutdown_count(self) -> int:
        """Get total number of explicit shutdown events handled."""
        return self._shutdown_count

    def get_disconnect_count(self) -> int:
        """Get total number of disconnect events handled."""
        return self._disconnect_count
