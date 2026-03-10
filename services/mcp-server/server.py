"""MCP server — Streamable HTTP transport with bearer token auth.

Internet-facing service at :443 (TLS). Exposes filesystem tools to
Anthropic models via the mcp_servers API parameter.

Security boundaries:
  - Bearer token auth via ASGI middleware rejects unauthenticated requests
  - TLS via Let's Encrypt certs mounted at /etc/letsencrypt (read-only)
  - Filesystem access sandboxed to /data/files via volume mount
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import sys
import time
from asyncio.transports import BaseTransport
from pathlib import Path
from typing import cast, override

import uvicorn
from fastmcp import FastMCP
from mcp_events import monotonic_now, record
from sse_starlette.sse import EventSourceResponse, ServerSentEvent
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from tools.browser import register_browser_tools
from tools.clip import register_clip_tools
from tools.context import register_context_tools
from tools.filesystem import register_filesystem_tools
from tools.project import register_project_tools
from tools.rag import register_rag_tools
from tools.sqlite import register_sqlite_tools
from tools.web import register_web_tools

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


def _env_truthy(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _patch_sse_ping() -> None:
    """Force sse_starlette to emit real SSE events every N seconds.

    Middleboxes and firewalls may categorise SSE comment-only pings as
    empty traffic.  Sending a named event with a payload resets idle
    timers.  We inject ping_message_factory and ping interval via kwargs
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


def _patch_sse_lifecycle_events() -> None:
    """Emit structured events for every SSE stream start/end."""
    _orig_stream = EventSourceResponse._stream_response  # type: ignore[attr-defined]

    async def _stream_with_events(self: EventSourceResponse, send: Send) -> None:
        t0 = monotonic_now()
        record("mcp.sse.stream.started")
        try:
            await _orig_stream(self, send)
        except Exception as exc:
            duration = monotonic_now() - t0
            record(
                "mcp.sse.stream.aborted",
                duration_s=round(duration, 3),
                reason=str(exc),
                exc_type=type(exc).__name__,
            )
            logger.warning("SSE stream aborted after %.1fs: %s", duration, exc)
            raise
        else:
            duration = monotonic_now() - t0
            record(
                "mcp.sse.stream.ended",
                duration_s=round(duration, 3),
                reason="clean",
            )
            logger.info("SSE stream ended cleanly after %.1fs", duration)

    EventSourceResponse._stream_response = _stream_with_events  # type: ignore[method-assign]


_patch_sse_ping()
_patch_sse_lifecycle_events()


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
    """ASGI middleware: bearer token auth + request lifecycle events.

    /health is exempt to allow Docker healthcheck without credentials.
    /clip handles CORS preflight without auth, POST with auth.
    Emits mcp.request.* events for all /mcp requests with timing.
    """

    _CLIPS_DIR = Path("/data/files/clips")
    _MAX_BODY_BYTES = 5 * 1024 * 1024
    _CORS_HEADERS: dict[str, str] = {
        "access-control-allow-origin": "*",
        "access-control-allow-methods": "POST, OPTIONS",
        "access-control-allow-headers": "Authorization, Content-Type",
        "access-control-max-age": "86400",
    }

    def __init__(self, app: ASGIApp, *, token: str) -> None:
        self._app: ASGIApp = app
        self._token: str = token

    @staticmethod
    def _slugify(text: str, max_len: int = 60) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", text.lower())
        slug = slug.strip("-")[:max_len].rstrip("-")
        return slug or "untitled"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request = Request(scope, receive)

        if request.url.path == "/health":
            response = JSONResponse({"status": "ok"})
            await response(scope, receive, send)
            return

        if request.url.path == "/clip":
            if request.method == "OPTIONS":
                response = JSONResponse({"status": "ok"}, headers=self._CORS_HEADERS)
                await response(scope, receive, send)
                return

            auth = request.headers.get("authorization", "")
            if auth != f"Bearer {self._token}":
                response = JSONResponse(
                    {"error": "Unauthorized"},
                    status_code=401,
                    headers=self._CORS_HEADERS,
                )
                await response(scope, receive, send)
                return

            if request.method == "POST":
                response = await self._handle_clip(request)
                await response(scope, receive, send)
                return

            response = JSONResponse(
                {"error": "Method not allowed"},
                status_code=405,
                headers=self._CORS_HEADERS,
            )
            await response(scope, receive, send)
            return

        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {self._token}":
            response = JSONResponse({"error": "Unauthorized"}, status_code=401)
            await response(scope, receive, send)
            return

        if request.url.path != "/mcp":
            await self._app(scope, receive, send)
            return

        client_ip = request.client.host if request.client else "unknown"
        method = request.method
        t0 = monotonic_now()

        record(
            "mcp.request.started",
            method=method,
            client_ip=client_ip,
        )

        try:
            await self._app(scope, receive, send)
        except Exception as exc:
            duration = monotonic_now() - t0
            record(
                "mcp.request.failed",
                method=method,
                client_ip=client_ip,
                duration_s=round(duration, 3),
                error=str(exc),
                exc_type=type(exc).__name__,
            )
            raise
        else:
            duration = monotonic_now() - t0
            record(
                "mcp.request.completed",
                method=method,
                client_ip=client_ip,
                duration_s=round(duration, 3),
            )

    async def _handle_clip(self, request: Request) -> JSONResponse:
        """Process a clip submission from the bookmarklet."""
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > self._MAX_BODY_BYTES:
                return JSONResponse(
                    {"error": "Payload too large (5MB limit)"},
                    status_code=413,
                    headers=self._CORS_HEADERS,
                )

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return JSONResponse(
                {"error": "Invalid JSON"},
                status_code=400,
                headers=self._CORS_HEADERS,
            )

        url = data.get("url", "").strip()
        title = data.get("title", "").strip()
        content = data.get("content", "").strip()
        selected = bool(data.get("selected", False))

        if not content:
            return JSONResponse(
                {"error": "Missing required field: content"},
                status_code=400,
                headers=self._CORS_HEADERS,
            )

        if not title:
            title = "Untitled Clip"

        ts = int(time.time())
        slug = self._slugify(title)
        filename = f"{slug}-{ts}.md"

        self._CLIPS_DIR.mkdir(parents=True, exist_ok=True)

        safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
        safe_url = url.replace("\\", "\\\\").replace('"', '\\"')
        frontmatter = (
            f"---\n"
            f'url: "{safe_url}"\n'
            f'title: "{safe_title}"\n'
            f"clipped_at: {ts}\n"
            f"selected: {str(selected).lower()}\n"
            f"chars: {len(content)}\n"
            f"---\n\n"
        )

        for attempt in range(5):
            candidate = self._CLIPS_DIR / (
                f"{slug}-{ts + attempt}.md" if attempt else filename
            )
            try:
                with candidate.open("x", encoding="utf-8") as clip_file:
                    clip_file.write(frontmatter + content)
                filename = candidate.name
                break
            except FileExistsError:
                continue
        else:
            return JSONResponse(
                {"error": "Unable to allocate unique clip filename"},
                status_code=409,
                headers=self._CORS_HEADERS,
            )
        logger.info(
            "clip: saved %s (%d chars, selected=%s)", filename, len(content), selected
        )

        return JSONResponse(
            {"status": "clipped", "clip_id": filename},
            headers=self._CORS_HEADERS,
        )


def _build_server() -> FastMCP:
    """Construct and configure the FastMCP application with all registered tools."""
    mcp: FastMCP = FastMCP("gateway-tools")
    register_filesystem_tools(mcp)
    register_project_tools(mcp)
    register_web_tools(mcp)
    register_rag_tools(mcp)
    if _env_truthy("ENABLE_CONTEXT_TOOLS", default=True):
        register_context_tools(mcp)
    else:
        logger.info("Context tools disabled (ENABLE_CONTEXT_TOOLS=false)")
    register_clip_tools(mcp)
    if _env_truthy("ENABLE_BROWSER_TOOLS", default=False):
        register_browser_tools(mcp)
    else:
        logger.info("Browser tools disabled (ENABLE_BROWSER_TOOLS=false)")
    register_sqlite_tools(mcp)

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
