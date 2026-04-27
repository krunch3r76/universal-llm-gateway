"""Filesystem tools package — sandboxed read/write/list in /data/files (cortex sandbox).

All paths are resolved relative to _SANDBOX_ROOT. Traversal attempts
(../) are rejected before resolution so that the container volume mount
is complemented by explicit code-level defense in depth.

The sandbox is the security boundary — no file extension restrictions are applied.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._files_dispatcher import register_files_tool
from ._tool_registrations import register_individual_tools

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_filesystem_tools(mcp: FastMCP) -> None:
    """Register all filesystem tools on *mcp*."""
    register_individual_tools(mcp)
    register_files_tool(mcp)
