"""
inference_djinn - Async Inference Worker Package

A standalone package for managing asynchronous inference workers using Unix sockets.
Supports multiple engines (GGUF, VLLM, ExLlamaV3) with automatic chat template detection.

Usage:
    import inference_djinn

    # Access engines
    engine = inference_djinn.engines.GGUFEngine(model_path="model.gguf")

    # Access utilities
    from inference_djinn.utils.streaming_core import emit_openai_stream

    # Access inspectors
    from inference_djinn.engines.gguf.inspector import load_gguf_metadata
"""

__version__ = "0.1.0"
__author__ = "Universal LLM Gateway"

# Make submodules importable
from . import engines, utils

# Harvest nominates these manage slugs when this lib lands (package-grain).
CONSUMERS: tuple[str, ...] = ('gateway',)

__all__ = ["engines", "utils", "__version__", "__author__"]
