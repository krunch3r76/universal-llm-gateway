"""State channel protocol definitions."""

from dataclasses import dataclass
from enum import Enum
from typing import Any


class MessageType(Enum):
    """State channel message types."""

    STATE_UPDATE = "state_update"
    STATE_DELTA = "state_delta"
    SUBSCRIBE = "subscribe"
    UNSUBSCRIBE = "unsubscribe"
    SYNC_REQUEST = "sync_request"
    SYNC_RESPONSE = "sync_response"
    HEARTBEAT = "heartbeat"


@dataclass
class StateUpdate:
    """Full state update message."""

    path: str
    value: Any
    timestamp: float
    version: int


@dataclass
class StateDelta:
    """Incremental state update."""

    path: str
    operation: str  # set, delete, append
    value: Any
    timestamp: float
    version: int


class StateProtocol:
    """Protocol for state channel communication."""

    @staticmethod
    def encode_update(update: StateUpdate) -> dict[str, Any]:
        """Encode state update for transmission."""
        return {
            "type": MessageType.STATE_UPDATE.value,
            "path": update.path,
            "value": update.value,
            "timestamp": update.timestamp,
            "version": update.version,
        }

    @staticmethod
    def encode_delta(delta: StateDelta) -> dict[str, Any]:
        """Encode state delta for transmission."""
        return {
            "type": MessageType.STATE_DELTA.value,
            "path": delta.path,
            "operation": delta.operation,
            "value": delta.value,
            "timestamp": delta.timestamp,
            "version": delta.version,
        }

    @staticmethod
    def decode_message(data: dict[str, Any]) -> Any:
        """Decode incoming message."""
        msg_type = data.get("type")

        if msg_type == MessageType.STATE_UPDATE.value:
            return StateUpdate(
                path=data["path"],
                value=data["value"],
                timestamp=data["timestamp"],
                version=data["version"],
            )
        elif msg_type == MessageType.STATE_DELTA.value:
            return StateDelta(
                path=data["path"],
                operation=data["operation"],
                value=data["value"],
                timestamp=data["timestamp"],
                version=data["version"],
            )

        return data
