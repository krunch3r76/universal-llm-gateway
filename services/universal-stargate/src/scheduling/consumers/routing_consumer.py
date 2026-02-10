"""
Routing consumer that updates routing tables based on gateway state events.

This consumer subscribes to GATEWAY_STATE_CHANGED events and maintains
a real-time view of available gateways for request routing.
"""

from universal_event_bus import Event, EventBus
from universal_logging import get_logger

from ..events import GATEWAY_STATE_CHANGED
from ..gateway_state import ConnectivityState, HealthState

logger = get_logger(__name__)


class RoutingConsumer:
    """
    Consumes gateway state events and maintains routing table.

    The routing table tracks which gateways are available for routing
    requests based on their connectivity and health states.
    """

    def __init__(self, event_bus: EventBus):
        """
        Initialize routing consumer.

        Args:
            event_bus: EventBus instance for event subscription
        """
        self.event_bus = event_bus
        self._available_gateways: set[str] = set()
        self._unreachable_gateways: set[str] = set()
        self._unhealthy_gateways: set[str] = set()
        self._gateway_states: dict[str, dict[str, str]] = {}

    def start(self):
        """Start consuming events"""
        # Subscribe to unified state change events (synchronous in EventBus v0.2.0)
        self.event_bus.subscribe_async(GATEWAY_STATE_CHANGED, self._handle_state_change)
        logger.info("✅ RoutingConsumer started")

    def stop(self):
        """Stop consuming events"""
        # Note: EventBus handlers persist for bus lifetime (no unsubscribe support)
        logger.info("RoutingConsumer stopped")

    async def _handle_state_change(self, event: Event):
        """
        Handle gateway state change events.

        Updates routing table based on connectivity and health states.
        """
        payload = event.payload
        url = payload["url"]
        connectivity = payload["connectivity"]
        health = payload["health"]

        # Update internal state tracking
        self._gateway_states[url] = {"connectivity": connectivity, "health": health}

        # Update routing sets based on new state
        is_available = (
            connectivity == ConnectivityState.REACHABLE.value
            and health == HealthState.HEALTHY.value
        )

        if is_available:
            if url not in self._available_gateways:
                self._available_gateways.add(url)
                self._unreachable_gateways.discard(url)
                self._unhealthy_gateways.discard(url)
                logger.info(f"🔄 Routing: Added gateway {url} to routing table")
        else:
            if url in self._available_gateways:
                self._available_gateways.remove(url)
                logger.info(f"🔄 Routing: Removed gateway {url} from routing table")

            # Track reason for unavailability
            if connectivity == ConnectivityState.UNREACHABLE.value:
                self._unreachable_gateways.add(url)
            elif health != HealthState.HEALTHY.value:
                self._unhealthy_gateways.add(url)

    def get_available_gateways(self) -> list[str]:
        """
        Get list of currently available gateways for routing.

        Returns:
            List of gateway URLs that are both reachable and healthy
        """
        return list(self._available_gateways)

    def is_gateway_available(self, url: str) -> bool:
        """
        Check if a specific gateway is available for routing.

        Args:
            url: Gateway URL to check

        Returns:
            True if gateway is available for routing
        """
        return url in self._available_gateways

    def get_routing_statistics(self) -> dict[str, int]:
        """
        Get routing statistics.

        Returns:
            Dictionary with counts of gateways in each state
        """
        return {
            "available": len(self._available_gateways),
            "unreachable": len(self._unreachable_gateways),
            "unhealthy": len(self._unhealthy_gateways),
            "total": len(self._gateway_states),
        }

    def get_gateway_state_summary(self) -> dict[str, dict[str, str]]:
        """
        Get detailed state information for all gateways.

        Returns:
            Dictionary mapping gateway URL to state information
        """
        return self._gateway_states.copy()
