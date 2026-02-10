"""
Core exception classes for universal_transport.

This module defines base exception classes used throughout the transport layer.
"""


class TransportError(Exception):
    """
    Base exception for transport-related errors.

    This is raised when transport operations (send/receive) fail.
    """

    pass


class UTConnectionError(TransportError):
    """
    Raised when connection establishment fails.

    This is a subclass of TransportError and should be used for transport-specific
    connection issues within the universal_transport ecosystem.
    """

    pass
