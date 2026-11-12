"""Client-side MCP tool-loop contract (Stargate in-process path).

Stargate must not HTTP-dial the MCP server for client-side tool loops. Only
tools executable via the in-process registry (cortex, agent_bus REST relays)
may run under ``mcp_tool_loop=True`` without ``remote_mcp``. Full MCP catalog
tools require the card-selected provider ``remote_mcp`` connector.

``universal_cloud_proxy.McpToolExecutor`` is out of scope here (leaf MCP client
per browser-resource-governance C1); this module governs Stargate/agent-seat
in-process dispatch only.
"""

from __future__ import annotations

from typing import Any

# In-process client tool loop — no MCP HTTP dial (C2). Canonical list lives here
# so ``llm_adapters`` does not import ``agent_seat`` (import-graph hygiene).
IN_PROCESS_CLIENT_TOOL_NAMES: frozenset[str] = frozenset({"cortex", "agent_bus"})

# Harvest nominates loaders that import this module (file-grain only).
CONSUMERS: tuple[str, ...] = ("cloud_proxy", "mcp", "stargate")

_MCP_HTTP_REFUSAL_MESSAGE = (
    "Client-side mcp_tool_loop cannot include MCP-server tools without "
    "remote_mcp=True; use remote_mcp (provider connector) or in-process tools "
    f"{sorted(IN_PROCESS_CLIENT_TOOL_NAMES)} only."
)


def client_loop_offenders(tools: list[dict[str, Any]] | None) -> list[str]:
    """Labels of entries Stargate cannot execute in-process on the client loop."""
    out: list[str] = []
    for entry in tools or []:
        if not isinstance(entry, dict):
            out.append("<type:unknown>")
            continue
        fn = entry.get("function")
        name = fn.get("name") if isinstance(fn, dict) else None
        if isinstance(name, str) and name in IN_PROCESS_CLIENT_TOOL_NAMES:
            continue
        kind = entry.get("type", "unknown")
        out.append(name if isinstance(name, str) and name else f"<type:{kind}>")
    return sorted(out)


def refusal_message(offending_tools: list[str]) -> str:
    """Human-readable refusal for ``ClientSideMcpHttpRefusedError``."""
    return f"{_MCP_HTTP_REFUSAL_MESSAGE} Offending tools: {offending_tools!r}."
