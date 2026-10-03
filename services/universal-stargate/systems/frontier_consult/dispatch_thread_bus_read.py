"""Agent-bus GET for the dispatch-thread prompt latch (friction a:37487).

Invariant: a transport failure must name the exception class even when
``str(exc)`` is empty, and a single retry covers UDS reset/timeout on a
thread that was just created.
"""

from __future__ import annotations

import os

import httpx
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client

_RETRYABLE = (httpx.TransportError, OSError)


def auth_headers() -> dict[str, str]:
    token = os.getenv("AGENT_BUS_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def format_bus_read_exc(exc: BaseException) -> str:
    """Qualify *exc* so callers never emit a trailing empty colon."""
    text = str(exc).strip()
    name = type(exc).__name__
    return f"{name}: {text}" if text else name


def bus_http_error_preview(resp: httpx.Response) -> str:
    """Body preview for a failed agent-bus status; never empty after a colon."""
    text = (resp.text or "").strip()
    return text[:200] if text else "(empty body)"


def _retryable_bus_read(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return False
    return isinstance(exc, _RETRYABLE)


async def get_agent_bus(path: str) -> httpx.Response:
    """GET *path* on agent-bus; retry once on transport/UDS failure."""
    last_exc: BaseException | None = None
    for attempt in (0, 1):
        try:
            async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=10.0) as client:
                return await client.get(path, headers=auth_headers())
        except _RETRYABLE as exc:
            last_exc = exc
            if attempt == 0 and _retryable_bus_read(exc):
                continue
            raise
    assert last_exc is not None
    raise last_exc
