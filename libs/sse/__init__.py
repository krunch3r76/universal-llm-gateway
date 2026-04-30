"""Server-Sent Events (SSE) — domain-agnostic primitives.

Pure RFC 9110 / W3C SSE handling. Zero LLM knowledge.

Modules:
    core: SSEMessage dataclass, format_sse_message/parse_sse_message,
          format_sse/parse_sse (JSON-only convenience surface).
    framing: iter_sse_events — bytes → SSEMessage async iterator (W3C).
    protocols: SSEReducer protocol, SSEStreamStats, exception hierarchy.
    accumulator: accumulate_sse_stream — driver loop with stall/overall
                 timeouts, reducer protocol invocation, on_event isolation.
"""

from sse.accumulator import DEFAULT_STALL_TIMEOUT_S, accumulate_sse_stream
from sse.core import (
    SSEMessage,
    format_sse,
    format_sse_message,
    parse_sse,
    parse_sse_message,
)
from sse.framing import iter_sse_events
from sse.protocols import (
    SSEError,
    SSEParseError,
    SSEProviderError,
    SSEReducer,
    SSEReductionError,
    SSEStallError,
    SSEStreamStats,
    SSETimeoutError,
)

__all__ = [
    # core
    "SSEMessage",
    "format_sse",
    "format_sse_message",
    "parse_sse",
    "parse_sse_message",
    # framing
    "iter_sse_events",
    # protocols / exceptions
    "SSEReducer",
    "SSEStreamStats",
    "SSEError",
    "SSEParseError",
    "SSEReductionError",
    "SSEStallError",
    "SSETimeoutError",
    "SSEProviderError",
    # accumulator
    "accumulate_sse_stream",
    "DEFAULT_STALL_TIMEOUT_S",
]
