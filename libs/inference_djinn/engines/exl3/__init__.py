"""
EXL3 engine package for inference_djinn.

Provides ExLlamaV3 engine specifically for EXL3 model inference.
This is the dedicated engine for EXL3 quantized models and offers
improved architecture support and device handling.
"""

from . import inspector


# Lazy import to avoid loading heavy dependencies at package import time
def __getattr__(name: str):
    if name == "ExLlamaV3Engine":
        from .engine import ExLlamaV3Engine

        return ExLlamaV3Engine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["ExLlamaV3Engine", "inspector"]
