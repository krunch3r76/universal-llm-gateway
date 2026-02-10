"""
Process lifecycle and communication management.

This package contains components for managing worker process lifecycle
and IPC communication operations.
"""

from .communication import ProcessCommunicationManager
from .lifecycle import ProcessLifecycleManager
from .state import ProcessState

__all__ = [
    "ProcessLifecycleManager",
    "ProcessCommunicationManager",
    "ProcessState",
]
