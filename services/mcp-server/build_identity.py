"""Stamp each tool result with the build that answered it.

``McpRequestEventsMiddleware`` already logs the caller. This middleware
writes the same kind of identity onto ``ToolResult.meta`` so a client
that only sees the tool response can name the serving checkout, the
sync time, the content hash, the container image, and the surface. It
does not record tokens or replace an existing ``meta`` entry other
than the ``ulg`` key.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from _deploy_stamp import read_source_sync_stamp
from deploy_identity.code_version import resolve_code_version
from fastmcp.server.middleware.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult
from request_profile import current_request_metadata

if TYPE_CHECKING:
    import mcp.types as mt
    from fastmcp import FastMCP


def build_ulg_meta() -> dict[str, str]:
    """Return the non-empty build fields for one tool result."""
    stamp = read_source_sync_stamp()
    request = current_request_metadata()
    fields = {
        "code_version": resolve_code_version() or "",
        "source_synced_at": stamp.get("source_synced_at") or "",
        "worktree_state": stamp.get("source_sync_worktree_state") or "",
        "source_tree_sha": stamp.get("source_tree_sha") or "",
        "image_id": stamp.get("image_id") or "",
        "surface": str(request.get("surface") or ""),
    }
    return {key: value for key, value in fields.items() if value}


class BuildIdentityMiddleware(Middleware):
    """Attach ``meta.ulg`` after the tool returns and before the wire encode."""

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Copy the deploy stamp onto the result without dropping other meta."""
        result = await call_next(context)
        meta: dict[str, Any] = dict(result.meta or {})
        meta["ulg"] = build_ulg_meta()
        result.meta = meta
        return result


def register_build_identity(mcp: FastMCP) -> None:
    """Register the build-stamp middleware on one surface server."""
    mcp.add_middleware(BuildIdentityMiddleware())
