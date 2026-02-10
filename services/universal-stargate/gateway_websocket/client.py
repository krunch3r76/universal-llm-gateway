"""WebSocket client for Gateway control plane.

Re-exports from ws_client.orchestrator for backward compatibility.
"""

from .ws_client import ConnectionState, GatewayWebSocketClient

__all__ = [
    "GatewayWebSocketClient",
    "ConnectionState",
]
