"""Cortex named MCP tools — provenance, resolution, staging extras, and boot.

These are individually registered tools (not part of the unified
cortex(tool=..., arguments=...) surface). Lower-frequency operations accessed via
dispatch(tool="cortex_brief", ...) etc.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._boot_runner import run_cortex_brief
from ._data_tools import register_data_tools
from ._orchestration_tools import register_orchestration_tools
from ._staging_tools import register_staging_tools
from ._surface_render import render_cortex_tool_description

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_cortex_named_tools(mcp: FastMCP, *, surface: str = "life") -> None:
    """Register named Cortex MCP tools: chunk, surface form, staging, and boot."""
    register_data_tools(mcp)
    register_staging_tools(mcp)
    register_orchestration_tools(mcp)


__all__ = [
    "register_cortex_named_tools",
    "run_cortex_brief",
    "render_cortex_tool_description",
]
