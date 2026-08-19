"""MCP recall tool — thin relay to cortex-api POST /graph/recall/*.

Life-primary sibling tool registered only on the /mcp/life surface; relays
matter and continuity recall to the G1 graph routes without composition here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._agent_tools import JsonArgStr, parse_dispatch_arguments
from ._cortex_relay import cx

if TYPE_CHECKING:
    from fastmcp import FastMCP

_MATTER_OP = "matter"
_CONTINUITY_OP = "continuity"
_OPS = frozenset({_MATTER_OP, _CONTINUITY_OP})


def register_recall_tools(mcp: FastMCP) -> None:
    """Register the life-only recall sibling tool on a FastMCP instance."""
    @mcp.tool(title="Recall")
    def recall(op: str, arguments: JsonArgStr = "{}") -> Any:
        """Life memory front door — matter / continuity recall (life surface).

        When/how: memory questions ("what do we know", "remember X", "where did
        we leave off") → recall, not cortex.search. matter = hub orientation;
        continuity = boot journal + open todos. Read-only card projection.
        See cortex-orientation § recall.
        """
        parsed = parse_dispatch_arguments(arguments)
        if parsed is None:
            return {"error": "arguments must be a JSON object", "status_code": 422}
        if op not in _OPS:
            return {
                "error": f"Unknown recall op {op!r}. Available: {sorted(_OPS)}",
                "status_code": 422,
            }
        return cx("POST", f"/graph/recall/{op}", parsed)
