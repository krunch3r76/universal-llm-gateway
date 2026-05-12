"""Agent-seat primitives: tool definitions, hydration, system-prompt assembly, native tool loop.

Shared between:
- MCP's ``frontier_dispatch`` (tool definitions + native tool loop via
  ``services/mcp-server/tools/_frontier_core.py`` — which delegates to
  ``native_loop.run_native_tool_loop``)
- Stargate's ``frontier_dispatch_v1`` pipeline handler (tool definitions +
  async hydration + native tool loop + system-prompt assembly)

Public surface (stable):

``TOOL_DEFINITIONS``        — static cortex dispatch fallback
``TEAM_TOOL_DEFINITIONS``   — full team toolset (cortex + agent_bus)

``hydrate_agent(agent, transcript_id=None)``    — async briefing fetch
``HydrationBundle``         — dataclass returned by ``hydrate_agent``

``assemble_system_prompt(agent, briefing_card_md=None, ...)`` — stack
``load_birth_prompt(agent)`` — raises on missing identity file
``build_subagent_preamble(agent)`` — Cortex-contribution guidance

``run_native_tool_loop(...)`` — transport-agnostic native tool loop
``NativeLoopResult`` / ``NativeToolCall`` — dataclasses
``NATIVE_PATHS``            — provider → native endpoint path map

``execute_tool(name, args)`` — async dispatcher for local tools + live MCP tools
``get_mcp_tool_definitions()`` — discover live MCP tool defs for client-side injection
``resolve_tool_definitions(names)`` — resolve static or live MCP tool names
"""

from __future__ import annotations

from agent_seat.executor import (
    execute_tool,
    get_mcp_tool_definitions,
    resolve_tool_definitions,
)
from agent_seat.hydration import AgentMeta, HydrationBundle, hydrate_agent
from agent_seat.native_loop import (
    NATIVE_PATHS,
    NativeLoopResult,
    NativeToolCall,
    run_native_tool_loop,
)
from agent_seat.profiles import (
    CapabilityProfile,
    RoleProfile,
    derive_inline_only,
    family_anchor,
    get_profile,
    get_role,
    load_profiles,
    load_roles,
    resolve_seat,
    role_anchor,
)
from agent_seat.prompts import (
    CORTEX_TOOL_QUICKREF,
    assemble_system_prompt,
    build_subagent_preamble,
)
from agent_seat.registry import normalize_agent_slug
from agent_seat.tools import (
    BRAVE_SEARCH_TOOL_DEFINITION,
    TEAM_TOOL_DEFINITIONS,
    TOOL_DEFINITIONS,
    TOOL_REGISTRY,
    resolve_tools,
)

__all__ = [
    "BRAVE_SEARCH_TOOL_DEFINITION",
    "CapabilityProfile",
    "CORTEX_TOOL_QUICKREF",
    "HydrationBundle",
    "AgentMeta",
    "NATIVE_PATHS",
    "NativeLoopResult",
    "NativeToolCall",
    "RoleProfile",
    "TEAM_TOOL_DEFINITIONS",
    "TOOL_DEFINITIONS",
    "TOOL_REGISTRY",
    "assemble_system_prompt",
    "build_subagent_preamble",
    "derive_inline_only",
    "execute_tool",
    "family_anchor",
    "get_mcp_tool_definitions",
    "get_profile",
    "get_role",
    "hydrate_agent",
    "load_profiles",
    "load_roles",
    "normalize_agent_slug",
    "resolve_seat",
    "resolve_tool_definitions",
    "role_anchor",
    "run_native_tool_loop",
    "resolve_tools",
]
