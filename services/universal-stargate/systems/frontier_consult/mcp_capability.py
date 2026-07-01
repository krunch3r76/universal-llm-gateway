"""Abstract MCP tool-access from mechanism for dispatch capability surfaces."""

from __future__ import annotations

from dataclasses import dataclass

from agent_seat.profiles import client_side_mcp_tool_loop_admitted
from model_id import ModelId

_REMOTE_MCP_PROVIDERS: frozenset[str] = frozenset({"anthropic"})


@dataclass(frozen=True, slots=True)
class McpCapability:
    tool_access: bool
    mcp_mechanism: str


def resolve_mcp_mechanism(
    *,
    substrate: str,
    model: str,
    remote_mcp: bool | None = None,
) -> str:
    """Return the MCP delivery mechanism without implying effective tool access."""
    if substrate == "sdk":
        return "local_native"
    if not client_side_mcp_tool_loop_admitted(model):
        return "none"
    provider = ModelId.parse(model).provider or ""
    if remote_mcp is False:
        return "client_side_injection"
    if remote_mcp is True:
        return "remote_connector" if provider in _REMOTE_MCP_PROVIDERS else "none"
    if provider in _REMOTE_MCP_PROVIDERS:
        return "remote_connector"
    return "client_side_injection"


def resolve_tool_access(
    *,
    substrate: str,
    model: str,
    mcp_enabled: bool = True,
    remote_mcp: bool | None = None,
    suppress_tools: bool = False,
) -> McpCapability:
    """Resolve effective tool access and mechanism as independent signals."""
    mechanism = resolve_mcp_mechanism(
        substrate=substrate,
        model=model,
        remote_mcp=remote_mcp,
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
    remote_mcp: bool | None = None,
    suppress_tools: bool = False,
) -> dict[str, bool | str]:
    """Caller-facing capability fragment for dispatch responses."""
    cap = resolve_tool_access(
        substrate=substrate,
        model=model,
        mcp_enabled=mcp_enabled,
        remote_mcp=remote_mcp,
        suppress_tools=suppress_tools,
    )
    return {
        "tool_access": cap.tool_access,
        "mcp_mechanism": cap.mcp_mechanism,
    }
