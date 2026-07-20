"""
Protocols for routing decision dependencies (duck-typed trackers).

Typing.Protocol surfaces for in-flight routing keys and related deps so
eviction planning stays type-safe without importing concrete Master trackers.
"""

from typing import Protocol


class RoutingKeyTracker(Protocol):
    """
    Protocol for tracking routing_keys with in-flight requests.

    Used by eviction planning to protect models from eviction while
    requests are being processed.

    Implemented by MasterRequestTracker.
    """

    def get_routing_keys_in_flight(self, gateway_id: str) -> set[str]:
        """
        Get routing_keys with in-flight requests on a specific gateway.

        Args:
            gateway_id: Gateway identifier (e.g., "edge-localhost-gateway")

        Returns:
            Set of routing_keys with active requests on this gateway.
        """
        ...

    def get_routing_keys_in_flight_globally(self) -> set[str]:
        """
        Get routing_keys with in-flight requests across ALL gateways.

        Returns:
            Set of routing_keys with active requests on any gateway.
        """
        ...
