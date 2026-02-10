"""Streaming response package for chat completions.

Public exports:
- generate_streaming_response: Main streaming response generator
- iter_error_and_complete_events: Error + completion event helper
"""

from .ndjson import iter_error_and_complete_events
from .response import generate_streaming_response

__all__ = [
    "generate_streaming_response",
    "iter_error_and_complete_events",
]
