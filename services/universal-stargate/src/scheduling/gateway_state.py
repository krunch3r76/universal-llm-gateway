"""
Gateway state data structures for centralized state management.

Provides clear separation between network connectivity and service health.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class ConnectivityState(Enum):
    """Network connectivity state - can we reach the gateway?"""

    REACHABLE = "reachable"
    UNREACHABLE = "unreachable"


class HealthState(Enum):
    """Service health state - is the gateway service functional?"""

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"  # Can't determine health (e.g., unreachable)


@dataclass
class GatewayState:
    """
    Composite gateway state combining connectivity and health.

    This provides a clear separation of concerns:
    - Connectivity: Network-level reachability
    - Health: Application-level service status
    """

    url: str
    connectivity: ConnectivityState
    health: HealthState
    last_check: datetime

    def is_available(self) -> bool:
        """
        Check if gateway is available for requests.

        A gateway is available if it's both reachable and healthy.
        """
        return (
            self.connectivity == ConnectivityState.REACHABLE
            and self.health == HealthState.HEALTHY
        )

    def has_changed(self, other: Optional["GatewayState"]) -> bool:
        """
        Check if this state differs from another state.

        Used to detect state transitions for logging.
        """
        if other is None:
            return True

        return self.connectivity != other.connectivity or self.health != other.health

    def get_transition_description(self, previous: Optional["GatewayState"]) -> str:
        """
        Get a human-readable description of the state transition.

        Returns a description suitable for logging state changes.
        """
        if previous is None:
            return f"Initial state: {self.connectivity.value}, {self.health.value}"

        changes = []
        if self.connectivity != previous.connectivity:
            changes.append(
                f"connectivity: {previous.connectivity.value} →"
                f"{self.connectivity.value}"
            )

        if self.health != previous.health:
            changes.append(f"health: {previous.health.value} → {self.health.value}")

        if not changes:
            return "No state change"

        return f"State transition: {', '.join(changes)}"

    def __str__(self) -> str:
        """String representation of gateway state."""
        return (
            f"GatewayState(url={self.url}, "
            f"connectivity={self.connectivity.value}, "
            f"health={self.health.value})"
        )
