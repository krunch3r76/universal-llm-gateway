"""
Event inspection utilities for gateway state management.

Provides debugging tools for analyzing state transitions and event publishing.
"""

from datetime import datetime
from typing import Any

from universal_event_bus import Event
from universal_logging import get_logger

from .events import GATEWAY_STATE_CHANGED

logger = get_logger(__name__)


class StateTransitionDebugger:
    """Debug helper for tracking and analyzing state transitions"""

    def __init__(self):
        self._transition_history: list[dict[str, Any]] = []
        self._event_counts: dict[str, int] = {}
        self._last_event_times: dict[str, float] = {}

    def record_transition(self, event: Event) -> None:
        """
        Record a state transition event for debugging.

        Args:
            event: State change event to record
        """
        if event.signal != GATEWAY_STATE_CHANGED:
            return

        payload = event.payload
        url = payload.get("url", "unknown")
        transition_type = payload.get("transition_type", "unknown")

        # Record transition
        self._transition_history.append(
            {
                "timestamp": datetime.now().isoformat(),
                "url": url,
                "transition_type": transition_type,
                "connectivity": payload.get("connectivity"),
                "health": payload.get("health"),
                "previous_connectivity": payload.get("previous_connectivity"),
                "previous_health": payload.get("previous_health"),
                "check_duration_ms": payload.get("check_duration_ms"),
            }
        )

        # Update counts
        key = f"{url}:{transition_type}"
        self._event_counts[key] = self._event_counts.get(key, 0) + 1

        # Keep history limited to prevent memory growth
        if len(self._transition_history) > 1000:
            self._transition_history = self._transition_history[-500:]

        logger.debug(
            f"State transition recorded: {url} | {transition_type} | "
            f"connectivity={payload.get('connectivity')} |"
            f"health={payload.get('health')}"
        )

    def get_transition_summary(self, url: str | None = None) -> dict[str, Any]:
        """
        Get summary of state transitions.

        Args:
            url: Optional gateway URL to filter by

        Returns:
            Dict with transition statistics
        """
        if url:
            filtered_history = [t for t in self._transition_history if t["url"] == url]
            filtered_counts = {
                k: v for k, v in self._event_counts.items() if k.startswith(f"{url}:")
            }
        else:
            filtered_history = self._transition_history
            filtered_counts = self._event_counts

        return {
            "total_transitions": len(filtered_history),
            "transition_counts": filtered_counts,
            "recent_transitions": filtered_history[-10:],  # Last 10
        }

    def get_recent_transitions(self, count: int = 10) -> list[dict[str, Any]]:
        """
        Get most recent state transitions.

        Args:
            count: Number of recent transitions to return

        Returns:
            List of recent transition records
        """
        return self._transition_history[-count:]

    def clear_history(self) -> None:
        """Clear all recorded transition history"""
        self._transition_history.clear()
        self._event_counts.clear()
        self._last_event_times.clear()
        logger.info("Cleared state transition history")


class EventRateLimiter:
    """
    Rate limiter to prevent event spam.

    Ensures events are not published too frequently for the same gateway.
    """

    def __init__(self, min_interval_seconds: float = 1.0):
        """
        Initialize rate limiter.

        Args:
            min_interval_seconds: Minimum seconds between events for same gateway
        """
        self.min_interval_seconds = min_interval_seconds
        self._last_event_times: dict[str, float] = {}

    def should_publish(self, url: str) -> bool:
        """
        Check if an event should be published based on rate limits.

        Args:
            url: Gateway URL

        Returns:
            bool: True if event should be published
        """
        import time

        current_time = time.time()
        last_time = self._last_event_times.get(url, 0)

        if current_time - last_time >= self.min_interval_seconds:
            self._last_event_times[url] = current_time
            return True

        return False

    def reset(self, url: str | None = None) -> None:
        """
        Reset rate limiter state.

        Args:
            url: Optional specific gateway to reset, or None to reset all
        """
        if url:
            self._last_event_times.pop(url, None)
        else:
            self._last_event_times.clear()


def format_state_transition_for_logging(event: Event) -> str:
    """
    Format a state transition event for human-readable logging.

    Args:
        event: State change event

    Returns:
        Formatted string suitable for logging
    """
    if event.signal != GATEWAY_STATE_CHANGED:
        return f"Event: {event.signal}"

    payload = event.payload
    url = payload.get("url", "unknown")
    transition_type = payload.get("transition_type", "unknown")
    connectivity = payload.get("connectivity", "unknown")
    health = payload.get("health", "unknown")
    prev_connectivity = payload.get("previous_connectivity")
    prev_health = payload.get("previous_health")
    duration_ms = payload.get("check_duration_ms", 0)

    if transition_type == "initial":
        return (
            f"[{url}] Initial state: connectivity={connectivity}, health={health} "
            f"(check_time={duration_ms}ms)"
        )

    transitions = []
    if prev_connectivity != connectivity:
        transitions.append(f"connectivity: {prev_connectivity} → {connectivity}")
    if prev_health != health:
        transitions.append(f"health: {prev_health} → {health}")

    return (
        f"[{url}] State transition ({transition_type}): {', '.join(transitions)} "
        f"(check_time={duration_ms}ms)"
    )


def validate_state_change_payload(
    payload: dict[str, Any],
) -> tuple[bool, str | None]:
    """
    Validate that a state change event payload has required fields.

    Args:
        payload: Event payload to validate

    Returns:
        Tuple of (is_valid, error_message)
    """
    required_fields = [
        "url",
        "connectivity",
        "health",
        "transition_type",
        "check_duration_ms",
    ]

    for field in required_fields:
        if field not in payload:
            return False, f"Missing required field: {field}"

    # Validate field values
    valid_connectivity = ["reachable", "unreachable"]
    if payload["connectivity"] not in valid_connectivity:
        return False, f"Invalid connectivity value: {payload['connectivity']}"

    valid_health = ["healthy", "unhealthy", "unknown"]
    if payload["health"] not in valid_health:
        return False, f"Invalid health value: {payload['health']}"

    valid_transitions = ["connectivity_only", "health_only", "both", "initial"]
    if payload["transition_type"] not in valid_transitions:
        return False, f"Invalid transition_type: {payload['transition_type']}"

    return True, None
