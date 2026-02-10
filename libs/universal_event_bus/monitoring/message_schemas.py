"""
Message schemas for UDP event monitoring.

Provides standardized message format for event transport and validation.
Based on UML Message structure with signal and payload.
"""

import json
from dataclasses import dataclass
from typing import Any


@dataclass
class MonitoringMessage:
    """
    Standard message schema for UDP event monitoring.

    Based on UML Message structure. All events are serialized to this
    format for transport.

    Attributes:
        signal: Event signal name (what happened)
        payload: Event data
        timestamp: ISO 8601 timestamp with Z suffix (auto-injected by EventBus)
        id: Global counter-based ID (auto-injected by EventBus)
        source: Optional source identifier
    """

    signal: str
    payload: Any
    timestamp: str
    id: int
    source: str | None = None

    @classmethod
    def from_event(cls, event, source: str | None = None) -> "MonitoringMessage":
        """
        Create MonitoringMessage from Event.

        Args:
            event: Event instance (with signal, payload, timestamp, id)
            source: Optional source identifier

        Returns:
            MonitoringMessage instance
        """
        # Import here to avoid circular dependency
        from ..events.event import Event

        if not isinstance(event, Event):
            raise TypeError(f"Expected Event instance, got {type(event).__name__}")

        if event.timestamp is None or event.id is None:
            raise ValueError(
                "Event must have timestamp and id set (should be injected by EventBus)"
            )

        return cls(
            signal=event.signal,
            payload=event.payload,
            timestamp=event.timestamp,
            id=event.id,
            source=source,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MonitoringMessage":
        """
        Create MonitoringMessage from dictionary.

        Args:
            data: Dictionary containing message fields

        Returns:
            MonitoringMessage instance
        """
        return cls(
            signal=data.get("signal", ""),
            payload=data.get("payload", {}),
            timestamp=data.get("timestamp", ""),
            id=data.get("id", 0),
            source=data.get("source"),
        )

    def to_dict(self) -> dict[str, Any]:
        """
        Convert message to dictionary.

        Returns:
            Dictionary representation
        """
        result = {
            "signal": self.signal,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "id": self.id,
        }
        if self.source is not None:
            result["source"] = self.source
        return result

    def to_json(self, max_size: int | None = None) -> str:
        """
        Serialize message to JSON string.

        Args:
            max_size: Optional maximum size in bytes (truncates data if exceeded)

        Returns:
            JSON string
        """
        json_str = json.dumps(self.to_dict(), separators=(",", ":"))

        if max_size and len(json_str.encode("utf-8")) > max_size:
            # Truncate data to fit within size limit
            truncated_msg = self.to_dict()
            truncated_msg["data"] = {"_truncated": True, "_original_type": self.type}
            json_str = json.dumps(truncated_msg, separators=(",", ":"))

        return json_str

    def validate(self) -> bool:
        """
        Validate message has required fields.

        Returns:
            True if valid, False otherwise
        """
        if not self.signal or not self.timestamp:
            return False

        if self.id is None or self.id < 0:
            return False

        # Validate timestamp format (basic check)
        if not self.timestamp.endswith("Z"):
            return False

        return True

    def get_size_bytes(self) -> int:
        """
        Get message size in bytes.

        Returns:
            Size in bytes
        """
        return len(self.to_json().encode("utf-8"))


def serialize_event(event, source: str | None = None) -> dict[str, Any]:
    """
    Serialize Event to dictionary format for UDP transport.

    Args:
        event: Event instance (with signal, payload, timestamp, id)
        source: Optional source identifier

    Returns:
        Dictionary representation
    """
    msg = MonitoringMessage.from_event(event, source)
    return msg.to_dict()


def serialize_event_to_json(
    event, source: str | None = None, max_size: int | None = None
) -> str:
    """
    Serialize Event to JSON string for UDP transport.

    Args:
        event: Event instance (with signal, payload, timestamp, id)
        source: Optional source identifier
        max_size: Optional maximum size in bytes

    Returns:
        JSON string
    """
    msg = MonitoringMessage.from_event(event, source)
    return msg.to_json(max_size)
