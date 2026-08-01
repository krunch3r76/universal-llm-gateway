"""GIW dispatch op names — denominator for openapi_mcp stamping.

Must stay in sync with ``services/mcp-server`` git + trigger MCP tools:
five standalone git tools (one op each) and ``trigger`` (schedule/list/get/cancel).
"""

from __future__ import annotations

_GIT_DISPATCH_OPS: frozenset[str] = frozenset(
    {
        "integrate",
        "land",
        "status",
        "diff",
        "commit",
    }
)

_TRIGGER_DISPATCH_OPS: frozenset[str] = frozenset(
    {
        "schedule",
        "list",
        "get",
        "cancel",
    }
)

GIW_DISPATCH_OPS: frozenset[str] = _GIT_DISPATCH_OPS | _TRIGGER_DISPATCH_OPS

# MCP catalog tool per dispatch op — mirrors x-mcp.tool on each route stamp.
DISPATCH_OP_CATALOG_TOOL: dict[str, str] = {
    "integrate": "git_integrate",
    "land": "git_land",
    "status": "git_status",
    "diff": "git_diff",
    "commit": "git_commit",
    "schedule": "trigger",
    "list": "trigger",
    "get": "trigger",
    "cancel": "trigger",
}
