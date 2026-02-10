"""
Sliding Window Buffer Package

Provides SLOC-compliant split of the EnhancedSlidingWindowBuffer class.
Original class was 1300 SLOC - split into focused modules.

Usage:
    from inference_djinn.engines.whisper.streaming.sliding_window import (
        AsyncSlidingWindowBuffer,
        SlidingWindowBuffer,  # Alias for AsyncSlidingWindowBuffer
    )
"""

from .core import AsyncSlidingWindowBuffer, SlidingWindowBuffer

__all__ = ["AsyncSlidingWindowBuffer", "SlidingWindowBuffer"]
