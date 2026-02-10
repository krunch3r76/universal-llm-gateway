"""
Workers module for Universal LLM Gateway.

This module contains all worker-related functionality including process management,
worker implementation, and high-level coordination.

Module-level accessor:
    get_worker_controller() - Returns the singleton WorkerController instance
    set_worker_controller() - Sets the singleton (called during app initialization)
"""

from typing import TYPE_CHECKING

# Re-export main classes for backward compatibility
from .controller import WorkerController
from .model_operations import UnloadResult
from .worker import Worker

if TYPE_CHECKING:
    pass

# Module-level singleton for worker controller access from non-FastAPI contexts (e.g., jobs)
_worker_controller: WorkerController | None = None


def get_worker_controller() -> WorkerController | None:
    """
    Get the global WorkerController instance.

    Returns the singleton WorkerController set during app initialization.
    Returns None if not yet initialized (e.g., during startup or in tests).

    This function enables access to the worker controller from contexts
    where FastAPI dependency injection is not available (e.g., background jobs).
    """
    return _worker_controller


def set_worker_controller(controller: WorkerController) -> None:
    """
    Set the global WorkerController instance.

    Called during app initialization in lifecycle.py.

    Args:
        controller: The WorkerController instance to set as global singleton
    """
    global _worker_controller
    _worker_controller = controller


__all__ = [
    "Worker",
    "WorkerController",
    "UnloadResult",
    "get_worker_controller",
    "set_worker_controller",
]
