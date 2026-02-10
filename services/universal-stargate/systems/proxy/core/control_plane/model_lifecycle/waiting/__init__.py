"""
Model waiting operations.

Event-driven waiting for model load/unload completion.
"""

from .handles import LoadResult, UnloadResult
from .waiter import ModelLoadWaiter

__all__ = [
    "ModelLoadWaiter",
    "LoadResult",
    "UnloadResult",
]
