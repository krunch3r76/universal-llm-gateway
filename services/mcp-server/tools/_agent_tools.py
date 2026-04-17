"""Tool surface for the multi-model agent loop.

Defines the OpenAI function-calling tool schemas and the executor that
dispatches tool calls to local Cortex + RAG REST endpoints.

Two tiers of tool definitions:

- ``TOOL_DEFINITIONS`` — lean read-only Cortex + RAG tools, injected for
  all boot levels except ``"none"``.
- ``TEAM_TOOL_DEFINITIONS`` — full Cortex dispatch + agent_bus, injected
  only for ``boot="team"`` or ``boot="full"`` to give team-member models
  write access to the knowledge graph and inter-agent communication.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

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


def _fn(
    name: str, desc: str, props: dict[str, Any], required: list[str] | None = None
) -> dict[str, Any]:
    """Build an OpenAI function-calling tool definition."""
    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return {
        "type": "function",
        "function": {"name": name, "description": desc, "parameters": schema},
    }


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    _fn(
        "cortex_entity_get",
        "Get a Cortex entity by ID with all its assertions and relationships. "
        "Entity IDs are type:slug format.",
        {
            "entity_id": {
                "type": "string",
                "description": "Entity ID in type:slug format (e.g. person:jane-doe)",
            }
        },
        ["entity_id"],
    ),
    _fn(
        "cortex_search_entities",
        "List Cortex entities, optionally filtered by type. Types include: "
        "person, account, legal_matter, org, decision, document, todo, trade, idea, service.",
        {
            "type": {"type": "string", "description": "Entity type filter (optional)"},
            "limit": {"type": "integer", "description": "Max results (default 30)"},
        },
    ),
    _fn(
        "cortex_assertions",
        "Query assertions across entities. Filter by entity_id, confidence level, or get recent.",
        {
            "entity_id": {
                "type": "string",
                "description": "Filter to one entity (optional)",
            },
            "confidence": {
                "type": "string",
                "enum": ["confirmed", "believed", "suspected", "hypothesized"],
                "description": "Confidence filter (optional)",
            },
            "limit": {"type": "integer", "description": "Max results (default 30)"},
        },
    ),
    _fn(
        "cortex_deadlines",
        "Get active deadlines and time-sensitive items from Cortex.",
        {},
    ),
    _fn(
        "rag_search",
        "Search the document corpus (research papers, legal docs, financial records, "
        "personal knowledge base). Returns retrieved context chunks with source labels.",
        {
            "query": {"type": "string", "description": "Natural language search query"},
            "scope": {
                "type": "string",
                "description": "Scope filter (e.g. research, knowledge_systems, project). "
                "Omit for all scopes.",
            },
        },
        ["query"],
    ),
]

TEAM_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    _fn(
        "cortex",
        "Cortex knowledge system — unified dispatch tool for the full Cortex "
        "surface. Extends the individual cortex_* read tools with write "
        "operations.\n\n"
        "Key operations:\n"
        "  entities (type?, limit?)  entity_get (entity_id)\n"
        "  entity_create (id, type, name, ...)  entity_update (entity_id, ...)\n"
        "  assertions (entity_id?, confidence?, limit?)\n"
        "  assert (entity_id, claim, confidence, evidence, ...)\n"
        "  observe (claim, entity_id?, agent?)  — lightweight observation\n"
        "  supersede (old_assertion_id, entity_id, claim, confidence, evidence, "
        "session_id, agent)\n"
        "  search (query, limit?)  deadlines ()\n"
        "  journal_read (limit?)  journal_write (timestamp, agent, summary, ...)\n"
        "  edge_create (session_id, agent, from_node, to_node, edge_type, ...)\n"
        "  edges (from_node?, to_node?, edge_type?, limit?)\n"
        "  edge_traverse (node, hops?, edge_type?)\n"
        "  review_queue (limit?)  activate (entity_ids, depth?, max_results?)\n"
        "  analyze_impact (entity_id, claim, confidence?)\n\n"
        "confidence: confirmed / believed / suspected / hypothesized\n"
        "arguments MUST be a JSON string, not a bare object.",
        {
            "tool": {
                "type": "string",
                "description": (
                    "Operation name (e.g. entity_get, assert, observe, "
                    "search, journal_write, edge_create)"
                ),
            },
            "arguments": {
                "type": "string",
                "description": (
                    "JSON string of operation arguments. "
                    'Example: \'{"entity_id": "person:jane-doe"}\''
                ),
            },
        },
        ["tool"],
    ),
    _fn(
        "agent_bus",
        "Inter-agent message bus — threads, turns, read/reply coordination.\n\n"
        "Operations:\n"
        "  fetch   (thread, last?, compact?, mark_read?) — get turns\n"
        "  reply   (thread, to, subject, body, after_turn, from_agent?) — reply\n"
        "  post    (slug, to, subject, body, from_agent?) — new thread\n"
        "  threads (status?) — list threads; status: active/archived/all\n"
        "  get     (thread, turn_number) — single turn lookup\n\n"
        "arguments MUST be a JSON string, not a bare object.",
        {
            "tool": {
                "type": "string",
                "description": "Operation: fetch, reply, post, threads, get",
            },
            "arguments": {
                "type": "string",
                "description": (
                    "JSON string of operation arguments. "
                    'Example: \'{"thread": "480", "last": 3, "compact": true}\''
                ),
            },
        },
        ["tool"],
    ),
]


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
    """Execute the unified cortex dispatch tool via the cortex ops table."""
    from .cortex import (
        _FRICTION_HINT,
        _OPS,
        _WORKFLOW_HINTS,
        _enrich_entity_completeness,
    )

    tool = args.get("tool", "")
    handler = _OPS.get(tool)
    if handler is None:
        return json.dumps(
            {"error": f"Unknown cortex tool {tool!r}. Available: {sorted(_OPS)}"}
        )

    parsed = _parse_dispatch_arguments(args.get("arguments", "{}"))
    if parsed is None:
        return json.dumps({"error": f"Invalid arguments JSON for cortex {tool!r}"})

    result = handler(**parsed)
    if not isinstance(result, dict):
        return json.dumps(result) if result is not None else "{}"
    if "error" in result:
        result["_hint"] = _FRICTION_HINT
    else:
        hint = _WORKFLOW_HINTS.get(tool)
        if hint:
            result["_next"] = hint
        if tool == "entity_get":
            _enrich_entity_completeness(result)
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
    if name == "cortex_entity_get":
        return json.dumps(_cx("GET", f"/entities/{args.get('entity_id', '')}"))

    if name == "cortex_search_entities":
        params = []
        if args.get("type"):
            params.append(f"type={args['type']}")
        params.append(f"limit={args.get('limit', 30)}")
        return json.dumps(_cx("GET", f"/entities?{'&'.join(params)}"))

    if name == "cortex_assertions":
        params = []
        if args.get("entity_id"):
            params.append(f"entity_id={args['entity_id']}")
        if args.get("confidence"):
            params.append(f"confidence={args['confidence']}")
        params.append(f"limit={args.get('limit', 30)}")
        return json.dumps(_cx("GET", f"/assertions?{'&'.join(params)}"))

    if name == "cortex_deadlines":
        return json.dumps(_cx("GET", "/deadlines"))

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
