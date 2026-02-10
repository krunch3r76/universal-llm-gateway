"""
Process management components for process-ipc package.

Provides process lifecycle management, worker abstractions,
and high-level interfaces for IPC communication.
"""

from .supervisor import ProcessSupervisor
from .worker import WorkerProcess

__all__ = [
    "ProcessSupervisor",
    "WorkerProcess",
]
