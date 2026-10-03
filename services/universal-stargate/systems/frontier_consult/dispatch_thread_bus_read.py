"""Agent-bus GET for the dispatch-thread prompt latch (friction a:37487).

Invariant: a transport failure must name the exception class even when
``str(exc)`` is empty. One retry is only for fast UDS/protocol failures —
never ``TimeoutException``. Two 10s timeouts would overrun MCP
``_RELAY_TIMEOUT`` (20s) and can admit a worker after the caller already
saw an error (agent-bus:14749#3).
"""

from __future__ import annotations

import os

import httpx
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client

# Fast failures only. TimeoutException is TransportError and must not be here.
_FAST_RETRYABLE = (
    httpx.ConnectError,
    httpx.ReadError,
    httpx.RemoteProtocolError,
    OSError,
)

AGENT_BUS_GET_TIMEOUT_S = 10.0
MCP_RELAY_TIMEOUT_S = 20.0


class AgentBusGetError(Exception):
    """Bus GET failed after the optional fast retry."""

    def __init__(self, exc: BaseException, *, retried: bool) -> None:
        self.exc = exc
        self.retried = retried
        super().__init__(format_bus_read_exc(exc, retried=retried))


def auth_headers() -> dict[str, str]:
    token = os.getenv("AGENT_BUS_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def format_bus_read_exc(exc: BaseException, *, retried: bool = False) -> str:
    """Qualify *exc* so callers never emit a trailing empty colon."""
    text = str(exc).strip()
    name = type(exc).__name__
    base = f"{name}: {text}" if text else name
    if retried:
        return f"{base} (after retry)"
    return base


def bus_http_error_preview(resp: httpx.Response) -> str:
    """Body preview for a failed agent-bus status; never empty after a colon."""
    text = (resp.text or "").strip()
    return text[:200] if text else "(empty body)"


async def _get_once(path: str) -> httpx.Response:
    async with make_async_client(
        DEFAULT_AGENT_BUS_URL, timeout=AGENT_BUS_GET_TIMEOUT_S
    ) as client:
        return await client.get(path, headers=auth_headers())


async def get_agent_bus(path: str) -> httpx.Response:
    """GET *path* on agent-bus; retry once on fast UDS/protocol failure only."""
    try:
        return await _get_once(path)
    except _FAST_RETRYABLE:
        try:
            return await _get_once(path)
        except _FAST_RETRYABLE as retry_exc:
            raise AgentBusGetError(retry_exc, retried=True) from retry_exc
