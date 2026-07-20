"""Gateway availability and lifecycle tracking for the scheduling core. Single responsibility: record each gateway's availability state (available, draining, or shutdown) as timestamped dataclass entries, giving other scheduling modules one place to read current gateway status."""

import time
from dataclasses import dataclass, field

from universal_logging import get_logger

logger = get_logger(__name__)


@dataclass
class GatewayInfo:
    """Information about a gateway instance."""

    gateway_id: str
    host: str
    port: int
    available: bool = True
    draining: bool = False
    last_seen: float = field(default_factory=time.time)
    shutdown_timestamp: float | None = None
    drain_timestamp: float | None = None


class GatewayStatusRegistry:
    """
    Tracks gateway availability and lifecycle state.

    Single-writer assumption: all mutations happen on one async event loop.
    No thread synchronization provided.
    """

    def __init__(self):
        self._gateways: dict[str, GatewayInfo] = {}

    def register_gateway(self, gateway_id: str, host: str, port: int) -> None:
        """Register a gateway as available."""
        self._gateways[gateway_id] = GatewayInfo(
            gateway_id=gateway_id,
            host=host,
            port=port,
            available=True,
            last_seen=time.time(),
        )
        # Handle Unix socket vs TCP transport in log message
        if host == "unix" and port == 0:
            # Unix socket transport (socket path logged by caller)
            logger.debug(
                f"Registered gateway in status registry: {gateway_id} (Unix socket)"
            )
        else:
            logger.info(f"Registered gateway: {gateway_id} at {host}:{port}")

    def mark_draining(self, gateway_id: str, timestamp: float | None = None) -> None:
        """
        Mark a gateway as draining (unavailable for new requests).

        In draining mode, in-flight requests may complete, but new requests
        should be routed elsewhere.

        Args:
            gateway_id: Gateway that is draining
            timestamp: Optional drain timestamp
        """
        if gateway_id not in self._gateways:
            logger.warning(f"Unknown gateway draining: {gateway_id}")
            return

        gateway = self._gateways[gateway_id]
        gateway.draining = True
        gateway.available = False
        gateway.drain_timestamp = timestamp or time.time()

        logger.info(f"Gateway {gateway_id} marked as draining")

    def mark_shutdown(self, gateway_id: str, timestamp: float) -> None:
        """
        Mark a gateway as shut down.

        Args:
            gateway_id: Gateway that is shutting down
            timestamp: Shutdown timestamp from event
        """
        if gateway_id not in self._gateways:
            logger.warning(f"Unknown gateway shutdown: {gateway_id}")
            return

        gateway = self._gateways[gateway_id]
        gateway.available = False
        gateway.draining = False
        gateway.shutdown_timestamp = timestamp

        logger.info(f"Gateway {gateway_id} marked as shutdown")

    def get_available_gateways(self) -> list[GatewayInfo]:
        """Get list of available gateways for routing (excludes draining)."""
        return [g for g in self._gateways.values() if g.available and not g.draining]

    def is_available(self, gateway_id: str) -> bool:
        """Check if a specific gateway is available."""
        gateway = self._gateways.get(gateway_id)
        return gateway is not None and gateway.available


# Global registry instance
gateway_status_registry = GatewayStatusRegistry()
