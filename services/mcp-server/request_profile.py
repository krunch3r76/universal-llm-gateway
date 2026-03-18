"""Request-scoped MCP profile context helpers.

Tools and middleware use this module to read/write the active profile without
coupling to transport-specific request objects.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_REQUEST_PROFILE: ContextVar[str] = ContextVar(
    "mcp_request_profile",
    default="default",
)


def current_profile() -> str:
    """Return the active request profile for the current execution context."""
    return _REQUEST_PROFILE.get()


@contextmanager
def bind_profile(profile: str) -> Iterator[None]:
    """Bind a request profile for the duration of a request dispatch scope."""
    token = _REQUEST_PROFILE.set(profile or "default")
    try:
        yield
    finally:
        _REQUEST_PROFILE.reset(token)
