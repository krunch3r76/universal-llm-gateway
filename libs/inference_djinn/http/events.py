"""Event definitions for OpenAI-compatible HTTP client."""

from universal_event_bus import Event, event_factory

CONTEXT_OVERFLOW_RETRIED = "inference.context.overflow.retried"
"""
Emitted when a context-length 400 is retried without max_tokens.

∀ retry: exactly one emission per retry attempt.

Payload:
    endpoint: str - The endpoint that returned 400
    original_max_tokens: int - The max_tokens value that was stripped
"""


@event_factory
def ContextOverflowRetried(endpoint: str, original_max_tokens: int) -> Event:  # noqa: N802
    """Create inference.context.overflow.retried event."""
    return Event(
        signal=CONTEXT_OVERFLOW_RETRIED,
        payload={
            "endpoint": endpoint,
            "original_max_tokens": original_max_tokens,
        },
    )
