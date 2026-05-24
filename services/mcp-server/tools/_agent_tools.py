"""Tool surface for the multi-model agent loop.

Sync tool-call dispatcher used by MCP's ``frontier_dispatch``. Tool schema
definitions are sourced from ``libs/agent_seat/tools.py`` (single source of
truth shared with the pipeline ``frontier_dispatch_v1`` handler). Cortex ops
relay to cortex-api ``POST /dispatch``; agent_bus uses ``.agent_bus.AGENT_BUS_OPS``.
"""

from __future__ import annotations

import json
from typing import Any

from agent_seat import (
    TEAM_TOOL_DEFINITIONS as TEAM_TOOL_DEFINITIONS,  # noqa: PLC0414 (re-export)
)
from agent_seat import (
    TOOL_DEFINITIONS as TOOL_DEFINITIONS,  # noqa: PLC0414 (re-export)
)

from ._cortex_relay import cx

SYSTEM_PROMPT = """\
You are an advisory agent with access to a structured knowledge system (Cortex).

## Cortex
Entities: people, accounts, legal matters, organizations, decisions, documents. \
Each has assertions — claims with confidence levels (confirmed, believed, \
suspected, hypothesized), evidence, and optional temporal scope (valid_from, \
valid_until for time-bounded facts like balances and due dates).

Entity IDs use type:slug format: person:jane-doe, decision:api-migration-v2, \
service:rag, todo:section-aware-chunking.

## Approach
1. Use tools to gather evidence before answering — check relevant entities, \
assertions, and relationships.
2. Give direct, actionable advice. Do not hedge unnecessarily.
3. Cite specific entities and assertions when referencing data.
4. If information conflicts, call it out explicitly.
5. State your confidence level and reasoning.\
"""


def parse_dispatch_arguments(raw: object) -> dict[str, Any] | None:
    """Parse dispatch-style arguments (JSON string or dict). None on failure.

    The MCP tool schemas declare ``arguments: string`` — that's the canonical
    wire form for every supported MCP client. Dict passthrough is retained as
    defense-in-depth for non-MCP callers that invoke the same handlers
    directly with already-parsed payloads (e.g. internal test helpers).
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _execute_cortex_dispatch(args: dict[str, Any]) -> str:
    """Execute the unified cortex dispatch tool via cortex-api POST /dispatch."""
    tool = args.get("tool", "")
    if not tool:
        return json.dumps({"error": "cortex: 'tool' is required"})

    parsed = parse_dispatch_arguments(args.get("arguments", "{}"))
    if parsed is None:
        return json.dumps({"error": f"Invalid arguments JSON for cortex {tool!r}"})

    result = cx("POST", "/dispatch", {"tool": tool, "arguments": parsed})
    return json.dumps(result)


def _execute_agent_bus_dispatch(args: dict[str, Any]) -> str:
    """Execute the unified agent_bus dispatch tool via the agent-bus ops table."""
    from .agent_bus import AGENT_BUS_OPS

    tool = args.get("tool", "")
    handler = AGENT_BUS_OPS.get(tool)
    if handler is None:
        return json.dumps(
            {
                "error": f"Unknown agent_bus tool {tool!r}. "
                f"Available: {sorted(AGENT_BUS_OPS)}"
            }
        )

    parsed = parse_dispatch_arguments(args.get("arguments", "{}"))
    if parsed is None:
        return json.dumps({"error": f"Invalid arguments JSON for agent_bus {tool!r}"})

    result = handler(**parsed)
    return json.dumps(result)


def execute_tool(name: str, args: dict[str, Any]) -> str:
    """Execute a tool call against local REST endpoints. Returns JSON string."""
    if name == "cortex":
        return _execute_cortex_dispatch(args)

    if name == "agent_bus":
        return _execute_agent_bus_dispatch(args)

    return json.dumps({"error": f"Unknown tool: {name}"})
