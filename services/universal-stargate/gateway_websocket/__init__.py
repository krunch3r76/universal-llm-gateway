"""
Gateway WebSocket client package.

Provides real-time connection to Gateway for:
- Model state events (loading, loaded, unloaded, busy, idle)
- Resource updates
- Catalog updates
- Query/response RPC

Architecture:
- client.py: Orchestration (connection lifecycle, state cache)
- handler/: Per-message handlers (SRP)
- event/: Event utilities (URL conversion, state transitions)
- messages.py: Protocol message types
"""

from .client import ConnectionState, GatewayWebSocketClient
from .messages import InitData, MessageType, ResourcesData

__all__ = [
    "GatewayWebSocketClient",
    "ConnectionState",
    "MessageType",
    "InitData",
    "ResourcesData",
]
