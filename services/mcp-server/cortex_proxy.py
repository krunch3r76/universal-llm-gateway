"""Reverse proxy — forward /cortex-api/* requests to cortex-api.

Routes through the local_api relay (synchronous httpx via UDS at
/tmp/universal-protocol/cortex-api.sock).  Auth is handled by the MCP
server's AuthMiddleware before requests reach this module.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from mcp_events import monotonic_now, record
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from tools._local_relay import relay as _relay

logger = logging.getLogger(__name__)

_PREFIX = "/cortex-api"

_CORS_HEADERS: dict[str, str] = {
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "GET, POST, PATCH, DELETE, OPTIONS",
    "access-control-allow-headers": "Authorization, Content-Type",
    "access-control-max-age": "86400",
    "vary": "Origin",
}

_FORWARDED_METHODS = frozenset({"GET", "POST", "PATCH", "PUT", "DELETE"})


def handle_cortex_preflight() -> JSONResponse:
    """Handle OPTIONS requests for CORS preflight."""
    return JSONResponse({"status": "ok"}, headers=_CORS_HEADERS)


async def handle_cortex_proxy(request: Request) -> Response:
    """Proxy a request to cortex-api via the local_api relay.

    Strips the ``/cortex-api`` prefix before forwarding.
    """
    path = request.url.path
    if path.startswith(_PREFIX):
        path = path[len(_PREFIX) :]
    if not path:
        path = "/"
    if request.url.query:
        path = f"{path}?{request.url.query}"

    method = request.method
    if method not in _FORWARDED_METHODS:
        return JSONResponse(
            {"error": "Method not allowed"},
            status_code=405,
            headers=_CORS_HEADERS,
        )

    body: dict[str, Any] | None = None
    if method in {"POST", "PATCH", "PUT"}:
        body = await request.json()

    t0 = monotonic_now()
    record("mcp.cortex.proxy.called", method=method, path=path)

    result = await asyncio.to_thread(_relay, "cortex-api", method, path, body)

    duration = monotonic_now() - t0

    if "error" in result:
        status_code = result.get("status_code", 502)
        record(
            "mcp.cortex.proxy.error",
            duration_s=round(duration, 3),
            error=result["error"],
            path=path,
        )
        return JSONResponse(result, status_code=status_code, headers=_CORS_HEADERS)

    record(
        "mcp.cortex.proxy.completed",
        duration_s=round(duration, 3),
        method=method,
        path=path,
        status=200,
    )
    return JSONResponse(result, headers=_CORS_HEADERS)
