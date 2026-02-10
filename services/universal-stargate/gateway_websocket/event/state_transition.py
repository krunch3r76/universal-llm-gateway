"""Gateway state transition computation."""

from dataclasses import dataclass
from enum import Enum


class ConnectivityValue(str, Enum):
    """Connectivity state values for event payloads."""

    REACHABLE = "reachable"
    UNREACHABLE = "unreachable"


class HealthValue(str, Enum):
    """Health state values for event payloads."""

    HEALTHY = "healthy"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class StateTransition:
    """
    Computed state transition for GATEWAY_STATE_CHANGED event.

    Pure data - no side effects.
    """

    url: str
    gateway_name: str
    connectivity: str
    health: str
    previous_connectivity: str | None
    previous_health: str | None
    transition_type: str
    check_duration_ms: int = 0


def compute_state_transition(
    connected: bool,
    previous_connected: bool | None,
    gateway_http_url: str,
    gateway_name: str,
) -> StateTransition | None:
    """
    Compute state transition for gateway connectivity change.

    Pure function: no side effects, no I/O.

    Args:
        connected: Current connection state
        previous_connected: Previous connection state (None if initial)
        gateway_http_url: Gateway HTTP URL
        gateway_name: Gateway name

    Returns:
        StateTransition if state changed, None if no transition

    Invariant:
        connected ⟺ connectivity = REACHABLE ∧ health = HEALTHY
        ¬connected ⟺ connectivity = UNREACHABLE ∧ health = UNKNOWN
    """
    # Only emit on actual transitions
    if previous_connected == connected:
        return None

    # Determine current state
    if connected:
        connectivity = ConnectivityValue.REACHABLE.value
        health = HealthValue.HEALTHY.value
    else:
        connectivity = ConnectivityValue.UNREACHABLE.value
        health = HealthValue.UNKNOWN.value

    # Determine previous state and transition type
    if previous_connected is None:
        prev_connectivity = None
        prev_health = None
        transition_type = "initial"
    elif previous_connected:
        prev_connectivity = ConnectivityValue.REACHABLE.value
        prev_health = HealthValue.HEALTHY.value
        transition_type = "both"
    else:
        prev_connectivity = ConnectivityValue.UNREACHABLE.value
        prev_health = HealthValue.UNKNOWN.value
        transition_type = "both"

    return StateTransition(
        url=gateway_http_url,
        gateway_name=gateway_name,
        connectivity=connectivity,
        health=health,
        previous_connectivity=prev_connectivity,
        previous_health=prev_health,
        transition_type=transition_type,
    )
