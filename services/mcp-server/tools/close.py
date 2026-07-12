"""MCP close tool — thin relay to cortex-api POST /close/*."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._agent_tools import JsonArgStr, parse_dispatch_arguments
from ._cortex_relay import cx

if TYPE_CHECKING:
    from fastmcp import FastMCP

_LIFE_OPS = frozenset({"stage", "draft", "check", "commit", "handoff"})
_CODE_MAINTENANCE_OPS = frozenset({"assemble", "audit"})
_ALL_OPS = _LIFE_OPS | _CODE_MAINTENANCE_OPS

_OP_PATHS = {
    "stage": "/close/stage",
    "draft": "/close/draft",
    "check": "/close/check",
    "commit": "/close/commit",
    "handoff": "/close/handoff",
}


def _close_relay(op: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if op in _OP_PATHS:
        return cx("POST", _OP_PATHS[op], arguments)
    if op == "assemble":
        return cx(
            "POST",
            "/dispatch",
            {"tool": "assemble_transcript", "arguments": arguments},
        )
    if op == "audit":
        return cx(
            "POST",
            "/dispatch",
            {"tool": "session_audit", "arguments": arguments},
        )
    return {
        "error": f"Unknown close op {op!r}. Available: {sorted(_ALL_OPS)}",
        "status_code": 422,
    }


def register_close_tools(mcp: FastMCP) -> None:
    @mcp.tool(title="Close")
    def close(op: str, arguments: JsonArgStr = "{}") -> Any:
        """Life close verb — stage/draft/check/commit/handoff over a server-side draft.

        Ordering: stage → draft* → check(pass) → commit → handoff (post-commit).
        Code maintenance ops: assemble (assemble_transcript), audit (session_audit).
        """
        parsed = parse_dispatch_arguments(arguments)
        if parsed is None:
            return {"error": "arguments must be a JSON object", "status_code": 422}
        if op not in _ALL_OPS:
            return {
                "error": f"Unknown close op {op!r}. Available: {sorted(_ALL_OPS)}",
                "status_code": 422,
            }
        return _close_relay(op, parsed)
