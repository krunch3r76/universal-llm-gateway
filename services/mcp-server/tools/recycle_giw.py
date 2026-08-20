"""Life-surface GIW recycle sliver — thin manage.sock relay.

Hard-scoped to git_integration_worker. No service or action parameters: the
caller cannot express any other fleet service. Decision logic (drain then
idle-escalate to force) lives in manage, not this handler.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tools.manage import _call_manage, _extract_result

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_recycle_giw_tools(mcp: FastMCP) -> None:
    """Register the life-surface GIW recycle verb on the current MCP surface."""

    @mcp.tool(title="Recycle GIW")
    def recycle_giw() -> dict[str, Any]:
        """Recycle git_integration_worker over manage.sock without the Auto queue.

        Drain-gated restart first; escalate to force only after occupant
        progress goes idle. Cannot target any other service. Full ``manage``
        stays code-only. Fire this from a life seat when the serial Auto
        slot is wedged — do not enqueue a repair job on that queue.
        """
        raw = _call_manage(
            {
                "jsonrpc": "2.0",
                "method": "recycle_giw",
                "params": {},
                "id": 1,
            },
            timeout=30.0,
        )
        return _extract_result(raw)


__all__ = ["register_recycle_giw_tools"]
