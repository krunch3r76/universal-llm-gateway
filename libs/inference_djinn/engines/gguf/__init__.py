"""
GGUF engine package for inference_djinn.

Provides both inference engine and model inspector for GGUF models.
"""

from . import inspector

# Lazy import to avoid loading heavy dependencies at package import time
def __getattr__(name: str):
    if name == "GGUFEngine":
        from .engine import GGUFEngine
        return GGUFEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = ["GGUFEngine", "inspector"]
