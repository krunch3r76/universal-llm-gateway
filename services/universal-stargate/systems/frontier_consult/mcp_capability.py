"""Abstract MCP tool-access from mechanism for dispatch capability surfaces."""

from __future__ import annotations

from dataclasses import dataclass

from agent_seat.profiles import client_side_mcp_tool_loop_admitted
from model_capabilities import mcp_remote_connector


@dataclass(frozen=True, slots=True)
class McpCapability:
    tool_access: bool
    mcp_mechanism: str


def resolve_mcp_mechanism(
    *,
    substrate: str,
    model: str,
) -> str:
    """Return the MCP delivery mechanism without implying effective tool access."""
    if substrate == "sdk":
        return "local_native"
    if not client_side_mcp_tool_loop_admitted(model):
        return "none"
    if mcp_remote_connector(model):
        return "remote_connector"
    return "client_side_injection"


def resolve_tool_access(
    *,
    substrate: str,
    model: str,
    mcp_enabled: bool = True,
    suppress_tools: bool = False,
) -> McpCapability:
    """Resolve effective tool access and mechanism as independent signals."""
    mechanism = resolve_mcp_mechanism(
        substrate=substrate,
        model=model,
    )
    if suppress_tools:
        return McpCapability(tool_access=False, mcp_mechanism=mechanism)
    if substrate == "sdk":
        return McpCapability(tool_access=True, mcp_mechanism=mechanism)
    if not mcp_enabled or mechanism == "none":
        return McpCapability(tool_access=False, mcp_mechanism=mechanism)
    return McpCapability(tool_access=True, mcp_mechanism=mechanism)


def mcp_capability_fields(
    *,
    substrate: str,
    model: str,
    mcp_enabled: bool = True,
    suppress_tools: bool = False,
) -> dict[str, bool | str]:
    """Caller-facing capability fragment for dispatch responses."""
    cap = resolve_tool_access(
        substrate=substrate,
        model=model,
        mcp_enabled=mcp_enabled,
        suppress_tools=suppress_tools,
    )
    return {
        "tool_access": cap.tool_access,
        "mcp_mechanism": cap.mcp_mechanism,
    }
