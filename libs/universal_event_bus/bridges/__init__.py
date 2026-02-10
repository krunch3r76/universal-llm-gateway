"""
Bridge components for universal_event_bus.

Provides bridges between EventBus and various transports.
"""

from .udp_bridge import UDPBridge

__all__ = ["UDPBridge"]
