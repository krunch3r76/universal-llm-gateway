"""
Transport layer for universal_event_bus.

Provides various transport mechanisms for event distribution.
"""

from .udp_transport import UDPTransport

__all__ = ["UDPTransport"]
