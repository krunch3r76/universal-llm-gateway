"""Artifact proxy — unauthenticated cortex-api access for claude.ai artifacts.

Experimental endpoint for testing direct artifact→cortex data ops without
routing through the model. Origin-validated: only requests from claude.ai
are accepted.

Security model:
  - Origin header must be https://claude.ai or https://www.claude.ai
  - CORS restricted to claude.ai (no wildcard)
  - Spoofable in theory, but sufficient for testing

Once validated, consider:
  - Short-lived session tokens from Claude chat
  - Request signing via artifact nonce
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from mcp_events import monotonic_now, record
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from tools.local_api import _relay

logger = logging.getLogger(__name__)

_PREFIX = "/artifact/cortex"

# Allowed origins — only claude.ai artifacts
_ALLOWED_ORIGINS = frozenset({
    "https://claude.ai",
    "https://www.claude.ai",
})

_FORWARDED_METHODS = frozenset({"GET", "POST", "PATCH", "PUT", "DELETE"})


def _cors_headers(origin: str | None) -> dict[str, str]:
    """Build CORS headers, restricting to allowed origins only."""
    # If origin is allowed, echo it back; otherwise omit (browser will block)
    allowed_origin = origin if origin in _ALLOWED_ORIGINS else ""
    return {
        "access-control-allow-origin": allowed_origin,
        "access-control-allow-methods": "GET, POST, PATCH, DELETE, OPTIONS",
        "access-control-allow-headers": "Content-Type",
        "access-control-max-age": "86400",
        "vary": "Origin",
    }


def _validate_origin(request: Request) -> str | None:
    """Return the origin if valid, None otherwise."""
    origin = request.headers.get("origin", "")
    if origin in _ALLOWED_ORIGINS:
        return origin
    return None


def handle_artifact_preflight(request: Request) -> JSONResponse:
    """Handle OPTIONS requests for CORS preflight."""
    origin = request.headers.get("origin", "")
    if origin not in _ALLOWED_ORIGINS:
        return JSONResponse(
            {"error": "Origin not allowed"},
            status_code=403,
        )
    return JSONResponse({"status": "ok"}, headers=_cors_headers(origin))


async def handle_artifact_proxy(request: Request) -> Response:
    """Proxy a request to cortex-api after origin validation.

    Strips the ``/artifact/cortex`` prefix before forwarding.
    """
    origin = _validate_origin(request)
    if origin is None:
        record(
            "mcp.artifact.proxy.rejected",
            reason="invalid_origin",
            origin=request.headers.get("origin", ""),
        )
        return JSONResponse(
            {"error": "Origin not allowed. This endpoint only accepts requests from claude.ai artifacts."},
            status_code=403,
        )

    path = request.url.path
    if path.startswith(_PREFIX):
        path = path[len(_PREFIX):]
    if not path:
        path = "/"
    if request.url.query:
        path = f"{path}?{request.url.query}"

    method = request.method
    if method not in _FORWARDED_METHODS:
        return JSONResponse(
            {"error": "Method not allowed"},
            status_code=405,
            headers=_cors_headers(origin),
        )

    body: dict[str, Any] | None = None
    if method in {"POST", "PATCH", "PUT"}:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                {"error": "Invalid JSON body"},
                status_code=400,
                headers=_cors_headers(origin),
            )

    t0 = monotonic_now()
    record("mcp.artifact.proxy.called", method=method, path=path)

    result = await asyncio.to_thread(_relay, "cortex-api", method, path, body)

    duration = monotonic_now() - t0

    if "error" in result:
        status_code = result.get("status_code", 502)
        record(
            "mcp.artifact.proxy.error",
            duration_s=round(duration, 3),
            error=result["error"],
            path=path,
        )
        return JSONResponse(result, status_code=status_code, headers=_cors_headers(origin))

    record(
        "mcp.artifact.proxy.completed",
        duration_s=round(duration, 3),
        method=method,
        path=path,
        status=200,
    )
    return JSONResponse(result, headers=_cors_headers(origin))
