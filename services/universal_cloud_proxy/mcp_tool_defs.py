"""Static tool definitions injected into Chat Completions for ``-mcp`` model IDs.

These mirror the curated tool surface from the MCP server's agent tools.
When a client sends ``openai/gpt-5.4-mcp`` to ``/v1/chat/completions``,
the cloud proxy injects these into ``body["tools"]`` before forwarding.
"""

from __future__ import annotations

from typing import Any


def _fn(
    name: str, desc: str, props: dict[str, Any], required: list[str] | None = None
) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return {
        "type": "function",
        "function": {"name": name, "description": desc, "parameters": schema},
    }


MCP_TOOL_DEFINITIONS: list[dict[str, Any]] = [
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
            "type": {"type": "string", "description": "Entity type filter (optional)"},
            "limit": {"type": "integer", "description": "Max results (default 30)"},
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
        "Search the document corpus (research papers, legal docs, financial "
        "records, personal knowledge base). Returns retrieved context chunks "
        "with source labels.",
        {
            "query": {
                "type": "string",
                "description": "Natural language search query",
            },
            "scope": {
                "type": "string",
                "description": (
                    "Scope filter (e.g. research, knowledge_systems, project). "
                    "Omit for all scopes."
                ),
            },
        },
        ["query"],
    ),
]
