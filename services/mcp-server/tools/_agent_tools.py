"""Tool surface for the multi-model agent loop.

Sync tool-call dispatcher used by MCP's ``frontier_generate``. Tool schema
definitions are sourced from ``libs/agent_seat/tools.py`` (single source of
truth shared with the pipeline ``frontier_dispatch_v1`` handler). Cortex ops
relay to cortex-api ``POST /dispatch``; agent_bus uses ``.agent_bus._AGENT_BUS_OPS``.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from agent_seat import (
    RAG_SEARCH_TOOL_DEFINITION as RAG_SEARCH_TOOL_DEFINITION,  # noqa: PLC0414 (re-export)
)
from agent_seat import (
    TEAM_TOOL_DEFINITIONS as TEAM_TOOL_DEFINITIONS,  # noqa: PLC0414 (re-export)
)
from agent_seat import (
    TOOL_DEFINITIONS as TOOL_DEFINITIONS,  # noqa: PLC0414 (re-export)
)

from ._cortex_relay import _cx

_STARGATE_URL = os.getenv("STARGATE_URL", "http://io:9999")

SYSTEM_PROMPT = """\
You are an advisory agent with access to a structured knowledge system (Cortex) \
and a document retrieval corpus (RAG).

## Cortex
Entities: people, accounts, legal matters, organizations, decisions, documents. \
Each has assertions — claims with confidence levels (confirmed, believed, \
suspected, hypothesized), evidence, and optional temporal scope (valid_from, \
valid_until for time-bounded facts like balances and due dates).

Entity IDs use type:slug format: person:jane-doe, decision:api-migration-v2, \
service:rag, todo:section-aware-chunking.

## RAG Corpus
Contains research papers, legal documents, financial records, project docs, \
and personal knowledge base entries. Search with natural language queries.

## Approach
1. Use tools to gather evidence before answering — check relevant entities, \
assertions, and documents.
2. Give direct, actionable advice. Do not hedge unnecessarily.
3. Cite specific entities and assertions when referencing data.
4. If information conflicts, call it out explicitly.
5. State your confidence level and reasoning.\
"""


def _parse_dispatch_arguments(raw: object) -> dict[str, Any] | None:
    """Parse dispatch-style arguments (JSON string or dict). None on failure.

    Both forms are advertised in the tool schema (``dict[str, Any] | str``) so
    remote-MCP clients whose provider-side validators reject string-typed
    ``arguments`` (xAI Responses API) can pass objects while legacy callers
    (Cursor, web clients) keep passing JSON strings unchanged.
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

    parsed = _parse_dispatch_arguments(args.get("arguments", "{}"))
    if parsed is None:
        return json.dumps({"error": f"Invalid arguments JSON for cortex {tool!r}"})

    result = _cx("POST", "/dispatch", {"tool": tool, "arguments": parsed})
    return json.dumps(result)


def _execute_agent_bus_dispatch(args: dict[str, Any]) -> str:
    """Execute the unified agent_bus dispatch tool via the agent-bus ops table."""
    from .agent_bus import _AGENT_BUS_OPS

    tool = args.get("tool", "")
    handler = _AGENT_BUS_OPS.get(tool)
    if handler is None:
        return json.dumps(
            {
                "error": f"Unknown agent_bus tool {tool!r}. "
                f"Available: {sorted(_AGENT_BUS_OPS)}"
            }
        )

    parsed = _parse_dispatch_arguments(args.get("arguments", "{}"))
    if parsed is None:
        return json.dumps({"error": f"Invalid arguments JSON for agent_bus {tool!r}"})

    result = handler(**parsed)
    return json.dumps(result)


def execute_tool(name: str, args: dict[str, Any]) -> str:
    """Execute a tool call against local REST endpoints. Returns JSON string."""
    if name == "rag_search":
        return _execute_rag_search(args)

    if name == "cortex":
        return _execute_cortex_dispatch(args)

    if name == "agent_bus":
        return _execute_agent_bus_dispatch(args)

    return json.dumps({"error": f"Unknown tool: {name}"})


def _execute_rag_search(args: dict[str, Any]) -> str:
    """Execute RAG search via Stargate pipeline."""
    query = args.get("query", "")
    body: dict[str, Any] = {
        "model": "rag-context",
        "messages": [{"role": "user", "content": query}],
    }
    scope = args.get("scope")
    if scope:
        body["pipeline_options"] = {"scope_override": scope}
    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(f"{_STARGATE_URL}/v1/chat/completions", json=body)
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        return json.dumps({"error": f"RAG search failed: {e}"})
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not content:
        return json.dumps({"error": "RAG returned empty results"})
    return content
