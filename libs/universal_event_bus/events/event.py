"""
UML Message-based Event structure for Universal Event Bus.

Based on UML Message specification with signal and payload.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class Event:
    """
    UML Message-based event structure.

    Based on UML specification where a Message consists of:
    - signal: The type/name of the event (what happened)
    - payload: The data associated with the event

    The system automatically injects:
    - timestamp: ISO 8601 timestamp with Z suffix
    - id: Global counter-based identifier

    These fields are managed by EventBus and should not be set by clients.

    IMPORTANT: Events MUST be created via factory functions decorated
    with @event_factory. Direct Event() construction is forbidden.

    Example (factory function):
        from universal_event_bus.events.factory import event_factory

        @event_factory
        def MyEvent(field: str) -> Event:
            return Event(signal="MyEvent", payload={"field": field})

        # Usage:
        event = MyEvent(field="value")
        await event_bus.publish_async(event)

        # EventBus automatically adds:
        # event.timestamp = "2025-10-06T03:15:30.123Z"
        # event.id = 42
    """

    signal: str
    payload: Any
    timestamp: str | None = field(default=None, init=False)
    id: int | None = field(default=None, init=False)

    def __post_init__(self):
        """Validate event structure and enforce factory function usage."""
        # Import here to avoid circular dependency
        from .factory import _allow_construction

        # Enforce factory function usage (thread-safe)
        if not getattr(_allow_construction, "value", False):
            raise RuntimeError(
                f"Event(signal='{self.signal}') must be created via factory functions. "
                + "Direct Event() construction is forbidden. "
                + "Use @event_factory decorator on factory functions. "
                + "See: .cursor/rules/architecture/patterns_ws.mdc#event-structure"
            )

        if not self.signal:
            raise ValueError("Event signal cannot be empty")

        # Payload can be any type, but dict is recommended
        # We don't enforce it to allow flexibility

    def to_dict(self) -> dict:
        """
        Convert event to dictionary.

        Returns:
            Dictionary with signal, payload, timestamp, and id
        """
        return {
            "signal": self.signal,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "id": self.id,
        }

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"Event(signal='{self.signal}', id={self.id}, timestamp={self.timestamp})"
        )


def create_timestamp() -> str:
    """
    Create ISO 8601 timestamp with Z suffix.

    Returns:
        Timestamp string in format: 2025-10-06T03:15:30.123Z
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
