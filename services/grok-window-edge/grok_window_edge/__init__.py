"""grok-window-edge: the io-side process that leases edge ops from Stargate.

Outbound only. It opens no listening socket, imports no cursor_bridge,
interlocutor, bus_watch or agent_bus_store module, carries no database DSN and
no watch directory, and in A1 opens no CDP connection and takes no lock.
"""

from __future__ import annotations

from .client import EdgeTransportError, HttpEdgeClient, StargateEdgeClient
from .loop import EdgeLoop
from .registry import HANDLERS, handler_for

__all__ = [
    "HANDLERS",
    "EdgeLoop",
    "EdgeTransportError",
    "HttpEdgeClient",
    "StargateEdgeClient",
    "handler_for",
]
