"""
Model eviction module for resource management.

Exports:
    - unload_models: Force unload models from gateway
"""

from .planner import unload_models

__all__ = [
    "unload_models",
]
