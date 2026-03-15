"""MCP request lifecycle middleware — emits ``mcp.request.*`` signals for ``/mcp``.

Sits inside the auth middleware so that rejected OAuth tokens terminate before
``mcp.request.started`` fires.  Includes ``auth_mode`` from the ASGI scope
(set by ``AuthMiddleware``) in every event payload for admission-path
observability.
"""

from __future__ import annotations

import json

from mcp_events import monotonic_now, record
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class McpRequestEventsMiddleware:
    """Emit ``mcp.request.*`` signals only for authenticated ``/mcp`` HTTP traffic."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request = Request(scope, receive)
        if request.url.path != "/mcp":
            await self._app(scope, receive, send)
            return

        client_ip = request.client.host if request.client else "unknown"
        method = request.method
        auth_mode = str(scope.get("auth_mode", "unknown"))
        t0 = monotonic_now()
        mcp_method = ""

        if method == "POST":
            msg = await receive()
            body = msg.get("body", b"")
            if body:
                try:
                    mcp_method = (
                        json.loads(body.decode("utf-8")).get("method", "") or ""
                    )
                except json.JSONDecodeError:
                    pass
            orig_receive = receive
            body_sent = False

            async def tee_receive() -> Message:
                nonlocal body_sent
                if not body_sent:
                    body_sent = True
                    return msg
                return await orig_receive()

            receive = tee_receive

        record(
            "mcp.request.started",
            method=method,
            client_ip=client_ip,
            mcp_method=mcp_method,
            auth_mode=auth_mode,
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
                auth_mode=auth_mode,
            )
            raise
        else:
            duration = monotonic_now() - t0
            record(
                "mcp.request.completed",
                method=method,
                client_ip=client_ip,
                duration_s=round(duration, 3),
                auth_mode=auth_mode,
            )
