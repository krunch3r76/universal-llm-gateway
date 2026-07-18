"""MCP cortex tool — thin relay to cortex-api POST /dispatch.

Op registry, handlers, workflow hints, friction suggestions, and entity
completeness enrichment live in cortex-api (libs/cortex_store/dispatch_ops/).
This module is the agent-facing MCP tool surface only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from request_profile import current_request_metadata
from tool_access import endpoint_op_allowed

from ._agent_tools import (
    JsonArgStr,
    dispatch_arguments_error,
    parse_dispatch_arguments,
)
from ._cortex_relay import cx
from .cortex_named_tools._surface_render import render_cortex_tool_description

if TYPE_CHECKING:
    from fastmcp import FastMCP

Surface = Literal["life", "code"]


def register_cortex_tools(mcp: FastMCP, surface: Surface = "life") -> None:
    """Register the dispatch-style cortex tool on the MCP server instance."""
    description = render_cortex_tool_description(surface)

    @mcp.tool(title="Cortex Knowledge Graph", description=description)
    def cortex(tool: str, arguments: JsonArgStr = "{}") -> Any:
        """Cortex knowledge system — see tool description for wire contract."""
        if parse_dispatch_arguments(arguments) is None:
            return dispatch_arguments_error(
                arguments, example='{"entity_id": "type:slug"}', tool="cortex"
            )
        req_surface = str(current_request_metadata().get("surface") or surface)
        allowed, rejection = endpoint_op_allowed(req_surface, "cortex", tool)
        if not allowed and rejection is not None:
            return {
                "error": rejection["hint"],
                "status_code": rejection["status_code"],
                "family": rejection["family"],
                "surface": rejection["surface"],
            }
        return cx(
            "POST",
            "/dispatch",
            {"tool": tool, "arguments": arguments},
            dispatch_tool=tool,
        )
