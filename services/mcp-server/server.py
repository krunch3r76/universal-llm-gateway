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
import sys

import uvicorn
from fastmcp import FastMCP
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


def _require_env(name: str) -> str:
    """Return the value of *name* from the environment, exiting if unset or empty."""
    value = os.environ.get(name, "").strip()
    if not value:
        logger.error("Required environment variable %s is not set", name)
        sys.exit(1)
    return value


class BearerAuthMiddleware:
    """ASGI middleware that enforces bearer token authentication.

    /health is exempt to allow Docker healthcheck without credentials.
    """

    def __init__(self, app: ASGIApp, *, token: str) -> None:
        self._app = app
        self._token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            request = Request(scope, receive)
            if request.url.path != "/health":
                auth = request.headers.get("authorization", "")
                if auth != f"Bearer {self._token}":
                    response = JSONResponse(
                        {"error": "Unauthorized"}, status_code=401
                    )
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
    uvicorn.run(
        protected_app,
        host=_HOST,
        port=_PORT,
        ssl_certfile=_CERT_FILE,
        ssl_keyfile=_KEY_FILE,
        log_level="info",
        # Anthropic's MCP client holds sessions open between tool calls;
        # the default 5s keep-alive drops the connection mid-agentic-loop.
        timeout_keep_alive=1800,
    )


if __name__ == "__main__":
    main()
