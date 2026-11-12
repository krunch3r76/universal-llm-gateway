"""Client-side MCP tool-loop contract (Stargate in-process path).

Stargate must not HTTP-dial the MCP server for client-side tool loops. Only
tools executable via the in-process registry (cortex, agent_bus REST relays)
may run under ``mcp_tool_loop=True`` without ``remote_mcp``. Full MCP catalog
tools require the card-selected provider ``remote_mcp`` connector.
"""

from __future__ import annotations

from typing import Any

from agent_seat.tools import IN_PROCESS_CLIENT_TOOL_NAMES

from llm_adapters.core import FrontierRequest

_MCP_HTTP_REFUSAL_MESSAGE = (
    "Client-side mcp_tool_loop cannot include MCP-server tools without "
    "remote_mcp=True; use remote_mcp (provider connector) or in-process tools "
    f"{sorted(IN_PROCESS_CLIENT_TOOL_NAMES)} only."
)


def openai_tool_names(tools: list[dict[str, Any]] | None) -> list[str]:
    if not tools:
        return []
    names: list[str] = []
    for entry in tools:
        if not isinstance(entry, dict):
            continue
        fn = entry.get("function")
        if isinstance(fn, dict):
            name = fn.get("name")
            if isinstance(name, str) and name:
                names.append(name)
    return names


def non_in_process_tool_names(tools: list[dict[str, Any]] | None) -> list[str]:
    return sorted(
        n for n in openai_tool_names(tools) if n not in IN_PROCESS_CLIENT_TOOL_NAMES
    )


def client_side_mcp_tool_loop_allowed(
    *,
    tools: list[dict[str, Any]] | None,
    remote_mcp: bool,
) -> bool:
    """Whether dispatch may set ``FrontierRequest.mcp_tool_loop=True``."""
    if remote_mcp or not tools:
        return False
    return not non_in_process_tool_names(tools)


def validate_client_side_mcp_tool_loop(req: FrontierRequest) -> None:
    """Raise ``ValueError`` when the client loop would require MCP HTTP."""
    if not req.mcp_tool_loop or req.remote_mcp:
        return
    offenders = non_in_process_tool_names(req.tools)
    if offenders:
        raise ValueError(
            f"{_MCP_HTTP_REFUSAL_MESSAGE} Offending tools: {offenders!r}."
        )
