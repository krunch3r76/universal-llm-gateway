"""Request-scoped MCP profile context helpers.

Tools and middleware use this module to read/write the active profile without
coupling to transport-specific request objects.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_REQUEST_PROFILE: ContextVar[str] = ContextVar(
    "mcp_request_profile",
    default="default",
)
_REQUEST_METADATA: ContextVar[dict[str, Any]] = ContextVar(
    "mcp_request_metadata",
    default={},
)
_REQUEST_STRUCTURED_CAPABLE: ContextVar[bool] = ContextVar(
    "mcp_request_structured_capable",
    default=False,
)


def current_profile() -> str:
    """Return the active request profile for the current execution context."""
    return _REQUEST_PROFILE.get()


def current_structured_capable() -> bool:
    """Return whether the active consumer reads structuredContent on the wire."""
    return _REQUEST_STRUCTURED_CAPABLE.get()


@contextmanager
def bind_request(
    profile: str,
    *,
    structured_capable: bool = False,
    **metadata: Any,
) -> Iterator[None]:
    """Bind request profile plus stable correlation metadata for one dispatch scope."""
    token = _REQUEST_PROFILE.set(profile or "default")
    meta_token = _REQUEST_METADATA.set(
        {
            key: value
            for key, value in metadata.items()
            if value is not None and value != ""
        }
    )
    cap_token = _REQUEST_STRUCTURED_CAPABLE.set(bool(structured_capable))
    try:
        yield
    finally:
        _REQUEST_STRUCTURED_CAPABLE.reset(cap_token)
        _REQUEST_METADATA.reset(meta_token)
        _REQUEST_PROFILE.reset(token)


def current_request_metadata() -> dict[str, Any]:
    """Return request-scoped metadata copied from the active dispatch context."""
    return dict(_REQUEST_METADATA.get())


@contextmanager
def bind_profile(profile: str) -> Iterator[None]:
    """Bind only a request profile for the duration of a request dispatch scope."""
    with bind_request(profile):
        yield
