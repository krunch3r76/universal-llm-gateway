"""
Model layer for Universal Stargate GUI v2.

Provides:
- Session data structures
- Memory backend for session storage
- Transport client for event reception
- Session management logic
"""

from .data_structures import RequestSession
from .memory_backend import MemoryBackend
from .session_manager import SessionManager
from .transport_client import TransportClient

__all__ = [
    "RequestSession",
    "MemoryBackend",
    "TransportClient",
    "SessionManager",
]
