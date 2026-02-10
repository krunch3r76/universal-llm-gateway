"""
Generic type/timestamp/data message envelope.

Service-agnostic wire format for WebSocket control-plane messages.
Used by Gateway WebSocket and Federation WebSocket.

Format:
{
  "type": "<message_type_string>",
  "timestamp": <unix_float>,
  "data": { ... }
}

Invariants:
  ∀ m: m.type ≠ "" ∧ isfinite(m.timestamp) ∧ isinstance(m.data, dict)
"""

import math
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True, kw_only=True)
class MessageEnvelope:
    """
    Generic message envelope for WebSocket wire format.

    Service-agnostic: type is str (not enum). Services define their own
    type enums and use this envelope for serialization/validation.
    """

    type: str
    timestamp: float = field(default_factory=time.time)
    data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate envelope structure."""
        if not self.type:
            raise ValueError("MessageEnvelope.type cannot be empty")
        if not math.isfinite(self.timestamp):
            raise ValueError(
                f"MessageEnvelope.timestamp must be finite: {self.timestamp}"
            )
        if not isinstance(self.data, dict):
            raise ValueError(f"MessageEnvelope.data must be dict: {type(self.data)}")

    def to_dict(self) -> dict[str, Any]:
        """Serialize to wire format."""
        return {
            "type": self.type,
            "timestamp": self.timestamp,
            "data": self.data,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MessageEnvelope":
        """
        Deserialize from wire format.

        Raises ValueError if structure invalid.
        """
        validate_envelope_dict(d)
        return cls(
            type=d["type"],
            timestamp=d.get("timestamp", time.time()),
            data=d.get("data", {}),
        )


def validate_envelope_dict(d: dict[str, Any]) -> None:
    """
    Validate dict has correct envelope structure.

    Raises ValueError with descriptive message if invalid.

    Invariant: ∀ valid d: "type" ∈ d ∧ d["type"] ≠ ""
    """
    if not isinstance(d, dict):
        raise ValueError(f"Expected dict, got {type(d).__name__}")

    if "type" not in d:
        raise ValueError("Missing required field: 'type'")

    msg_type = d["type"]
    if not isinstance(msg_type, str) or not msg_type:
        raise ValueError(f"Field 'type' must be non-empty string: {msg_type!r}")

    if "timestamp" in d:
        ts = d["timestamp"]
        if not isinstance(ts, int | float) or not math.isfinite(ts):
            raise ValueError(f"Field 'timestamp' must be finite number: {ts!r}")

    if "data" in d:
        data = d["data"]
        if not isinstance(data, dict):
            raise ValueError(f"Field 'data' must be dict: {type(data).__name__}")
