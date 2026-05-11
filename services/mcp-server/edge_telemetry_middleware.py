"""Edge telemetry middleware — pre-auth catch-all access log.

Records ``mcp.edge.request.observed`` for every HTTP request that reaches
the Starlette app, regardless of path, method, or auth status.  Sits as
the OUTERMOST ASGI middleware so it observes traffic that AuthMiddleware
short-circuits (``/health``, CORS preflights on ``/clip`` /
``/workbench/relay`` / ``/cortex-api`` / ``/llm/*``) and traffic that
later middleware records under different signals (``/mcp`` lifecycle,
OAuth route hits, unauthorized 401s).

Sanitized payload — never includes credential-bearing header values:

  - ``client_ip``, ``client_port``: socket-level peer
  - ``method``, ``path``, ``host``: request line + Host header
  - ``user_agent``, ``accept``, ``referer``: pure UA / negotiation hints
  - ``has_authorization``: boolean only — token bytes are never logged
  - ``query_keys``: sorted parameter names only, values dropped

Intent: closest-to-edge access record we can produce without a separate
reverse proxy.  Useful for diagnosing whether a remote client (e.g. xAI's
``grok.com`` connector backend) is actually reaching the box and on which
URL surface.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from mcp_events import record
from starlette.requests import Request

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

_MAX_UA_LENGTH = 256
_MAX_PATH_LENGTH = 512
_MAX_HOST_LENGTH = 128
_MAX_ACCEPT_LENGTH = 256
_MAX_REFERER_LENGTH = 256
_MAX_QUERY_KEYS = 32


class EdgeTelemetryMiddleware:
    """ASGI middleware that emits a single observation event per HTTP request.

    Wraps the entire downstream stack — including AuthMiddleware — so that
    requests rejected for missing auth, requests served by short-circuit
    branches, and successfully authenticated requests all produce one
    edge-layer record.  Telemetry failures never propagate to the caller.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        try:
            request = Request(scope)
            payload = _build_observation(request)
            record("mcp.edge.request.observed", **payload)
        except Exception:
            # ¬ break inbound traffic on telemetry failure.
            logger.warning(
                "edge_telemetry: failed to record observation", exc_info=True
            )

        await self._app(scope, receive, send)


def _build_observation(request: Request) -> dict[str, object]:
    """Return the sanitized observation payload for ``request``.

    Constructed without consuming the request body — only ASGI scope and
    headers are read.  All header values that could contain secrets are
    reduced to booleans or dropped.
    """
    headers = request.headers
    client_ip = request.client.host if request.client else "unknown"
    client_port = request.client.port if request.client else 0
    path = request.url.path[:_MAX_PATH_LENGTH]
    user_agent = headers.get("user-agent", "")[:_MAX_UA_LENGTH]
    host = headers.get("host", "")[:_MAX_HOST_LENGTH]
    accept = headers.get("accept", "")[:_MAX_ACCEPT_LENGTH]
    referer = headers.get("referer", "")[:_MAX_REFERER_LENGTH]
    has_authorization = bool(headers.get("authorization"))
    query_keys = sorted(request.query_params.keys())[:_MAX_QUERY_KEYS]

    return {
        "client_ip": client_ip,
        "client_port": client_port,
        "method": request.method,
        "path": path,
        "host": host,
        "user_agent": user_agent,
        "accept": accept,
        "referer": referer,
        "has_authorization": has_authorization,
        "query_keys": query_keys,
    }
