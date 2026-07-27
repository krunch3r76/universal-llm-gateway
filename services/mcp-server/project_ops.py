"""Workspaces fs op implementations — bound independently of MCP wire registration.

``register_project_tools`` remains code-surface wire registration only (AC4).
Life-surface ``fs`` workspaces reads resolve callables from this registry at
the ``_fs_impl`` chokepoint instead of ``overflow_registry``.
"""

from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


@lru_cache(maxsize=1)
def workspaces_impl_registry() -> dict[str, Callable[..., Any]]:
    """Project tool callables keyed by wire name (``read_project_file``, etc.)."""
    from fastmcp import FastMCP

    from tools.project import register_project_tools

    mcp = FastMCP("project-impl-bindings")
    register_project_tools(mcp)

    async def _collect() -> dict[str, Callable[..., Any]]:
        registry: dict[str, Callable[..., Any]] = {}
        for tool in await mcp.list_tools():
            tool_obj = await mcp.get_tool(tool.name)
            registry[tool.name] = tool_obj.fn
        return registry

    return asyncio.run(_collect())
