"""Thin MCP relay for the host grok window routing read surface."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations
from transport_utils import DEFAULT_STARGATE_URL, make_sync_client

if TYPE_CHECKING:
    from fastmcp import FastMCP

_ROUTE = "/v1/grok/window-routing"
_TIMEOUT = 30.0


def fetch_grok_routing_read(*, stargate_url: str | None = None) -> dict[str, Any]:
    """GET the host routing snapshot via Stargate (no local bindings I/O)."""
    base = (stargate_url or os.environ.get("STARGATE_URL") or DEFAULT_STARGATE_URL).strip()
    try:
        with make_sync_client(base, timeout=_TIMEOUT) as client:
            response = client.get(_ROUTE)
    except OSError as exc:
        return {
            "ok": False,
            "error": {
                "code": "stargate_unreachable",
                "message": str(exc)[:400],
                "source": "services/mcp-server/tools/grok_routing_read",
                "retryable": True,
                "data": {"route": _ROUTE},
            },
        }
    try:
        body = response.json()
    except Exception as exc:
        return {
            "ok": False,
            "error": {
                "code": "invalid_json",
                "message": str(exc)[:200],
                "source": "services/mcp-server/tools/grok_routing_read",
                "retryable": True,
                "data": {"status_code": response.status_code},
            },
        }
    if response.status_code >= 400:
        if isinstance(body, dict) and body.get("error"):
            return body
        return {
            "ok": False,
            "error": {
                "code": "http_error",
                "message": f"HTTP {response.status_code}",
                "source": "services/mcp-server/tools/grok_routing_read",
                "retryable": response.status_code >= 500,
                "data": {"status_code": response.status_code, "body": body},
            },
        }
    return body if isinstance(body, dict) else {"ok": False, "error": {"code": "invalid_body", "message": "non-object JSON", "source": "services/mcp-server/tools/grok_routing_read", "retryable": False, "data": {}}}


def register_grok_routing_read_tool(mcp: FastMCP) -> None:
    @mcp.tool(
        title="Grok window routing (read-only)",
        annotations=ToolAnnotations(readOnlyHint=True),
    )
    def grok_routing_read() -> dict[str, Any]:
        """Read-only grok window routing snapshot from the host Stargate surface.

        Returns default_window, grok_bot_home, and preferred_spawn (when set)
        from a single routing epoch. Relay only — no local state file access.
        """
        return fetch_grok_routing_read()


__all__ = ["fetch_grok_routing_read", "register_grok_routing_read_tool"]
