"""DEPRECATED: import from `sse` instead.

This module re-exports the SSE primitives from their new home at `libs/sse/`
to give external satellite consumers a one-version migration window.

Slated for deletion after the next satellite-repo sweep. New code MUST
import from `sse` directly.
"""

import warnings

from sse.core import (
    SSEMessage,
    format_sse,
    format_sse_message,
    parse_sse,
    parse_sse_message,
)

warnings.warn(
    "universal_protocol.sse is deprecated; import from `sse` instead "
    "(libs/sse/). This shim will be removed in the next sweep.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "SSEMessage",
    "format_sse",
    "format_sse_message",
    "parse_sse",
    "parse_sse_message",
]
