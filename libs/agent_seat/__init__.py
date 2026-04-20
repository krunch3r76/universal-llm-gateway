"""Agent-seat primitives: tool definitions, hydration, system-prompt assembly, native tool loop.

Shared between:
- MCP's ``frontier_generate`` (tool definitions + native tool loop via
  ``services/mcp-server/tools/_frontier_core.py`` — which delegates to
  ``native_loop.run_native_tool_loop``)
- Stargate's ``frontier_dispatch_v1`` pipeline handler (tool definitions +
  async hydration + native tool loop + system-prompt assembly)

Public surface (stable):

``TOOL_DEFINITIONS``        — lean read-only tools (cortex_* + rag_search)
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

``execute_tool(name, args)`` — async dispatcher for cortex/agent_bus/rag_search
"""

from __future__ import annotations

from agent_seat.executor import execute_tool
from agent_seat.hydration import HydrationBundle, hydrate_agent
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
)

__all__ = [
    "CORTEX_TOOL_QUICKREF",
    "HydrationBundle",
    "NATIVE_PATHS",
    "NativeLoopResult",
    "NativeToolCall",
    "RAG_SEARCH_TOOL_DEFINITION",
    "TEAM_TOOL_DEFINITIONS",
    "TOOL_DEFINITIONS",
    "assemble_system_prompt",
    "build_subagent_preamble",
    "execute_tool",
    "hydrate_agent",
    "load_birth_prompt",
    "run_native_tool_loop",
]
