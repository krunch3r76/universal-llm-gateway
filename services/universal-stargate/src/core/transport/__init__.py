"""
Event transport layer for Universal Stargate monitoring.

Provides pluggable transports (Unix socket, UDP, TCP) for broadcasting
monitoring events from the proxy to GUI and remote clients.
"""

from .base import EventTransport
from .server import TransportServer
from .tcp_stream import TCPStreamTransport
from .udp_datagram import UDPDatagramTransport
from .unix_stream import UnixStreamTransport

__all__ = [
    "EventTransport",
    "UnixStreamTransport",
    "UDPDatagramTransport",
    "TCPStreamTransport",
    "TransportServer",
]
