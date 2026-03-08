"""MCP server — Streamable HTTP transport with bearer token auth.

Internet-facing service at :443 (TLS). Exposes filesystem tools to
Anthropic models via the mcp_servers API parameter.

Security boundaries:
  - Bearer token auth via ASGI middleware rejects unauthenticated requests
  - TLS via Let's Encrypt certs mounted at /etc/letsencrypt (read-only)
  - Filesystem access sandboxed to /data/files via volume mount
"""

from __future__ import annotations

import logging
import os
import socket
import sys
from asyncio.transports import BaseTransport
from typing import cast, override

import uvicorn
from fastmcp import FastMCP
from sse_starlette.sse import EventSourceResponse, ServerSentEvent
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from tools.filesystem import register_filesystem_tools
from tools.project import register_project_tools

logger = logging.getLogger(__name__)

_AUTH_TOKEN_ENV = "MCP_AUTH_TOKEN"
_CERT_FILE = "/etc/letsencrypt/live/mcp.k-1.me/fullchain.pem"
_KEY_FILE = "/etc/letsencrypt/live/mcp.k-1.me/privkey.pem"
_HOST = "0.0.0.0"
_PORT = 443
_TCP_KEEPIDLE = 10
_TCP_KEEPINTVL = 10
_TCP_KEEPCNT = 3
_SSE_PING_INTERVAL: int = int(os.getenv("MCP_SSE_PING_INTERVAL", "15"))


def _patch_sse_ping() -> None:
    """Force sse_starlette to emit real SSE events every N seconds.

    Sophos XGS (and similar HTTP-inspecting firewalls) categorise SSE
    comment-only pings as empty traffic and start an idle-stream timer
    (~60 s default).  Sending a named event with a payload resets that
    timer.  We inject ping_message_factory and ping interval via kwargs
    so they're set during the original __init__ constructor path.
    """
    _orig_init = EventSourceResponse.__init__

    def _patched_init(
        self: EventSourceResponse, *args: object, **kwargs: object
    ) -> None:
        kwargs["ping_message_factory"] = lambda: ServerSentEvent(
            event="heartbeat", data="{}"
        )
        kwargs["ping"] = _SSE_PING_INTERVAL
        _orig_init(self, *args, **kwargs)

    EventSourceResponse.__init__ = _patched_init  # type: ignore[method-assign]


def _patch_sse_disconnect_logging() -> None:
    """Log every SSE stream termination to identify where drops occur."""
    _orig_stream = EventSourceResponse._stream_response  # type: ignore[attr-defined]

    async def _stream_with_log(self: EventSourceResponse, send: Send) -> None:
        try:
            await _orig_stream(self, send)
        except Exception as exc:
            logger.warning("SSE stream aborted: %s", exc, exc_info=True)
            raise
        else:
            logger.info("SSE stream closed (client disconnect or end-of-data)")

    EventSourceResponse._stream_response = _stream_with_log  # type: ignore[method-assign]


_patch_sse_ping()
_patch_sse_disconnect_logging()


def _require_env(name: str) -> str:
    """Return the value of *name* from the environment, exiting if unset or empty."""
    value = os.environ.get(name, "").strip()
    if not value:
        logger.error("Required environment variable %s is not set", name)
        sys.exit(1)
    return value


def _set_tcp_keepalive(sock: socket.socket) -> None:
    """Enable TCP keepalive with short probe intervals.

    Prevents NAT/firewall connection tracking from evicting idle TCP
    sessions during long model-thinking pauses between MCP tool calls.
    """
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    if hasattr(socket, "TCP_KEEPIDLE"):
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, _TCP_KEEPIDLE)
    if hasattr(socket, "TCP_KEEPINTVL"):
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, _TCP_KEEPINTVL)
    if hasattr(socket, "TCP_KEEPCNT"):
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, _TCP_KEEPCNT)


class BearerAuthMiddleware:
    """ASGI middleware that enforces bearer token authentication.

    /health is exempt to allow Docker healthcheck without credentials.
    """

    def __init__(self, app: ASGIApp, *, token: str) -> None:
        self._app: ASGIApp = app
        self._token: str = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            request = Request(scope, receive)
            if request.url.path == "/health":
                response = JSONResponse({"status": "ok"})
                await response(scope, receive, send)
                return
            auth = request.headers.get("authorization", "")
            if auth != f"Bearer {self._token}":
                response = JSONResponse({"error": "Unauthorized"}, status_code=401)
                await response(scope, receive, send)
                return
        await self._app(scope, receive, send)


def _build_server() -> FastMCP:
    """Construct and configure the FastMCP application with all registered tools."""
    mcp: FastMCP = FastMCP("gateway-tools")
    register_filesystem_tools(mcp)
    register_project_tools(mcp)

    @mcp.tool()
    def health() -> dict[str, str]:
        """Health check — confirms the MCP server is reachable."""
        return {"status": "ok"}

    return mcp


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    for cert_path in (_CERT_FILE, _KEY_FILE):
        if not os.path.exists(cert_path):
            logger.error("TLS file not found: %s", cert_path)
            sys.exit(1)

    auth_token = _require_env(_AUTH_TOKEN_ENV)
    mcp = _build_server()

    # Wrap the FastMCP ASGI app with bearer auth middleware
    asgi_app = mcp.http_app(transport="streamable-http")
    protected_app = BearerAuthMiddleware(asgi_app, token=auth_token)

    logger.info("Starting MCP server on %s:%d", _HOST, _PORT)
    config = uvicorn.Config(
        protected_app,
        host=_HOST,
        port=_PORT,
        ssl_certfile=_CERT_FILE,
        ssl_keyfile=_KEY_FILE,
        log_level="info",
        timeout_keep_alive=1800,
    )
    config.load()

    orig_protocol_class = config.http_protocol_class

    class KeepaliveProtocol(orig_protocol_class):
        @override
        def connection_made(self, transport: BaseTransport) -> None:
            sock = cast(socket.socket | None, transport.get_extra_info("socket"))
            if sock is not None:
                _set_tcp_keepalive(sock)
            super().connection_made(transport)

    config.http_protocol_class = KeepaliveProtocol
    server = uvicorn.Server(config)
    server.run()


if __name__ == "__main__":
    main()
