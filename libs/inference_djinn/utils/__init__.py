"""
Utility functions for inference_djinn.

Provides core streaming utilities used by engines.
"""

from .streaming_core import emit_openai_stream, iterate_blocking

__all__ = ["iterate_blocking", "emit_openai_stream"]
