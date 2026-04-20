"""Tool-call schemas for agent-seat tool loops.

OpenAI function-calling definitions shared between the MCP ``frontier_generate``
path and the pipeline ``frontier_dispatch_v1`` handler. Both surfaces use the same
OpenAI-shape tool schema — providers that speak native Anthropic / xAI / Google
formats are translated by upstream adapters (MCP's ``llm_adapters``;
Stargate's cloud-proxy).

Two tiers:

- ``TOOL_DEFINITIONS`` — lean read-only Cortex + RAG tools.
- ``TEAM_TOOL_DEFINITIONS`` — full Cortex dispatch + ``agent_bus`` for team
  members that need write access to the knowledge graph and inter-agent
  communication.

``RAG_SEARCH_TOOL_DEFINITION`` is the single RAG entry reused in both tiers.
"""

from __future__ import annotations

from typing import Any


def _fn(
    name: str,
    desc: str,
    props: dict[str, Any],
    required: list[str] | None = None,
) -> dict[str, Any]:
    """Build an OpenAI function-calling tool definition."""
    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return {
        "type": "function",
        "function": {"name": name, "description": desc, "parameters": schema},
    }


RAG_SEARCH_TOOL_DEFINITION: dict[str, Any] = _fn(
    "rag_search",
    "Search the document corpus (research papers, legal docs, financial records, "
    "personal knowledge base). Returns retrieved context chunks with source labels.",
    {
        "query": {"type": "string", "description": "Natural language search query"},
        "scope": {
            "type": "string",
            "description": (
                "Scope filter (e.g. research, knowledge_systems, project). "
                "Omit for all scopes."
            ),
        },
    },
    ["query"],
)


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
        "person, account, legal_matter, org, decision, document, todo, trade, "
        "idea, service.",
        {
            "type": {
                "type": "string",
                "description": "Entity type filter (optional)",
            },
            "limit": {
                "type": "integer",
                "description": "Max results (default 30)",
            },
        },
    ),
    _fn(
        "cortex_assertions",
        "Query assertions across entities. Filter by entity_id, confidence "
        "level, or get recent.",
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
            "limit": {
                "type": "integer",
                "description": "Max results (default 30)",
            },
        },
    ),
    _fn(
        "cortex_deadlines",
        "Get active deadlines and time-sensitive items from Cortex.",
        {},
    ),
    RAG_SEARCH_TOOL_DEFINITION,
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
