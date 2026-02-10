"""Master-side WebSocket server (accepts connections from remotes)."""

from .server import MasterWebSocketServer, WSMasterReceiver

__all__ = ["MasterWebSocketServer", "WSMasterReceiver"]
