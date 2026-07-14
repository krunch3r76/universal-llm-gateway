"""MCP imprint tool — thin relay to cortex-api POST /graph/imprint/*."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._agent_tools import JsonArgStr, parse_dispatch_arguments
from ._cortex_relay import cx

if TYPE_CHECKING:
    from fastmcp import FastMCP

_PROPOSE_OP = "propose"
_COMMIT_OP = "commit"
_REMEMBER_OP = "remember"
_OPS = frozenset({_PROPOSE_OP, _COMMIT_OP, _REMEMBER_OP})


def register_imprint_tools(mcp: FastMCP) -> None:
    @mcp.tool(title="Imprint")
    def imprint(op: str, arguments: JsonArgStr = "{}") -> Any:
        """Life imprint — propose / commit / remember (life surface).

        When/how: natural save/pin → remember if entity ids fully resolved;
        propose if fuzzy/multi-match; commit only by proposal_id after preview.
        Patch = cortex.life/v1 JSON-LD. See agent_skill:life-imprint-when-how.
        """
        parsed = parse_dispatch_arguments(arguments)
        if parsed is None:
            return {"error": "arguments must be a JSON object", "status_code": 422}
        if op not in _OPS:
            return {
                "error": f"Unknown imprint op {op!r}. Available: {sorted(_OPS)}",
                "status_code": 422,
            }
        if op == _PROPOSE_OP:
            return cx("POST", "/graph/imprint/propose", parsed)
        if op == _COMMIT_OP:
            return cx("POST", "/graph/imprint/commit", parsed)
        return cx("POST", "/graph/imprint/remember", parsed)
