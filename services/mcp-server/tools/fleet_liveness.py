"""MCP wrapper for the manage-authoritative fleet liveness snapshot.

The life and code surfaces call the same read-only manage JSON-RPC method so
operators receive identical evidence and cannot accidentally bypass the
load-surface-specific uncertainty rules.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tools.manage import _call_manage, _extract_result

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_fleet_liveness_tools(mcp: FastMCP) -> None:
    """Register the direct fleet liveness verb on the current MCP surface."""

    @mcp.tool(title="Fleet Liveness")
    def fleet_liveness() -> dict[str, Any]:
        """Return fresh service markers, dirty paths, and honest load evidence.

        Container-copy services use hashes from their running load location.
        Host-process and bind-mounted services expose temporal or indeterminate
        evidence without promoting start time into proof of execution.
        """
        raw = _call_manage(
            {
                "jsonrpc": "2.0",
                "method": "fleet_liveness",
                "params": {},
                "id": 1,
            },
            timeout=30.0,
        )
        return _extract_result(raw)


__all__ = ["register_fleet_liveness_tools"]
