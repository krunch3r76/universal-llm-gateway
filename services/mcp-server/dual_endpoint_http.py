"""Dual MCP streamable-HTTP mounts — ``/mcp/life`` + ``/mcp/code``.

Each mount is a separate FastMCP instance with a surface-filtered ``tools/list``.
Bare ``/mcp`` is intentionally absent after cutover.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING

from endpoint_surface import MCP_CODE_PATH, MCP_LIFE_PATH, surface_from_path
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.types import ASGIApp, Receive, Scope, Send

if TYPE_CHECKING:
    from fastmcp import FastMCP

MCP_ENDPOINT_PATHS = frozenset({MCP_LIFE_PATH, MCP_CODE_PATH})


class SurfaceStampMiddleware:
    """Stamp ``scope['mcp_surface']`` from the mount prefix before tool dispatch."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            surface = surface_from_path(scope.get("path", ""))
            if surface is not None:
                scope["mcp_surface"] = surface
        await self._app(scope, receive, send)


def build_dual_endpoint_app(
    life_mcp: FastMCP,
    code_mcp: FastMCP,
) -> Starlette:
    """Build parent Starlette app with life + code sub-mounts (inner path ``/``)."""
    life_app = life_mcp.http_app(
        transport="streamable-http",
        stateless_http=True,
        path="/",
    )
    code_app = code_mcp.http_app(
        transport="streamable-http",
        stateless_http=True,
        path="/",
    )

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        """Run both mounted FastMCP session managers with the parent app."""
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(life_app.lifespan(app))
            await stack.enter_async_context(code_app.lifespan(app))
            yield

    routes = [
        Mount(MCP_LIFE_PATH, app=life_app),
        Mount(MCP_CODE_PATH, app=code_app),
    ]
    app = Starlette(routes=routes, lifespan=lifespan)
    return app


def is_mcp_endpoint_path(path: str) -> bool:
    """True when ``path`` is a dual-endpoint MCP streamable-HTTP mount."""
    return path in MCP_ENDPOINT_PATHS
