"""Agent-seat primitives: tool definitions, hydration, system-prompt assembly, native tool loop.

Shared between:
- MCP's ``frontier_generate`` (tool definitions + native tool loop via
  ``services/mcp-server/tools/_frontier_core.py`` — which delegates to
  ``native_loop.run_native_tool_loop``)
- Stargate's ``frontier_dispatch_v1`` pipeline handler (tool definitions +
  async hydration + native tool loop + system-prompt assembly)

Public surface (stable):

``TOOL_DEFINITIONS``        — read tier (cortex dispatch + rag_search)
``TEAM_TOOL_DEFINITIONS``   — full team toolset (cortex + agent_bus)
``RAG_SEARCH_TOOL_DEFINITION`` — single RAG entry reused in both tiers

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
from agent_seat.prompts import (
    CORTEX_TOOL_QUICKREF,
    assemble_system_prompt,
    build_subagent_preamble,
    load_birth_prompt,
)
from agent_seat.tools import (
    RAG_SEARCH_TOOL_DEFINITION,
    TEAM_TOOL_DEFINITIONS,
    TOOL_DEFINITIONS,
    TOOL_REGISTRY,
    resolve_tools,
)

__all__ = [
    "CORTEX_TOOL_QUICKREF",
    "HydrationBundle",
    "AgentMeta",
    "NATIVE_PATHS",
    "NativeLoopResult",
    "NativeToolCall",
    "RAG_SEARCH_TOOL_DEFINITION",
    "TEAM_TOOL_DEFINITIONS",
    "TOOL_DEFINITIONS",
    "TOOL_REGISTRY",
    "assemble_system_prompt",
    "build_subagent_preamble",
    "execute_tool",
    "get_mcp_tool_definitions",
    "hydrate_agent",
    "load_birth_prompt",
    "resolve_tool_definitions",
    "run_native_tool_loop",
    "resolve_tools",
]
