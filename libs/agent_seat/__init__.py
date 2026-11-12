"""Agent-seat primitives: tool definitions, hydration, system-prompt assembly, native tool loop.

Shared between:
- MCP's ``team_dispatch`` relay (tool definitions + native tool loop via
  Stargate ``frontier_dispatch_v1`` — ``native_loop.run_native_tool_loop``)
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

``execute_tool(name, args)`` — async dispatcher for in-process tools only (C2)
``in_process_client_tool_definitions()`` — in-process client tool defs (no MCP HTTP dial)
"""

from __future__ import annotations

from agent_seat.executor import execute_tool
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
    known_families,
    load_lead_agent_slugs,
    load_profiles,
    load_roles,
    resolve_seat,
    role_anchor,
    seat_to_family,
)
from agent_seat.prompts import (
    CORTEX_TOOL_QUICKREF,
    assemble_system_prompt,
    build_subagent_preamble,
)
from agent_seat.registry import is_lead_agent, normalize_agent_slug
from agent_seat.tools import (
    STATIC_TOOL_FALLBACK,
    TEAM_TOOL_DEFINITIONS,
    TOOL_DEFINITIONS,
    TOOL_REGISTRY,
    in_process_client_tool_definitions,
    resolve_tools,
)

# Harvest nominates these manage slugs when this lib lands (package-grain).
CONSUMERS: tuple[str, ...] = (
    "cloud_proxy",
    "git_integration_worker",
    "mcp",
    "stargate",
)

__all__ = [
    "CapabilityProfile",
    "CORTEX_TOOL_QUICKREF",
    "HydrationBundle",
    "AgentMeta",
    "NATIVE_PATHS",
    "NativeLoopResult",
    "NativeToolCall",
    "RoleProfile",
    "STATIC_TOOL_FALLBACK",
    "TEAM_TOOL_DEFINITIONS",
    "TOOL_DEFINITIONS",
    "TOOL_REGISTRY",
    "assemble_system_prompt",
    "build_subagent_preamble",
    "derive_inline_only",
    "execute_tool",
    "family_anchor",
    "get_profile",
    "get_role",
    "hydrate_agent",
    "in_process_client_tool_definitions",
    "is_lead_agent",
    "known_families",
    "load_lead_agent_slugs",
    "load_profiles",
    "load_roles",
    "normalize_agent_slug",
    "resolve_seat",
    "role_anchor",
    "run_native_tool_loop",
    "seat_to_family",
    "resolve_tools",
]
