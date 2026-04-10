"""Static tool definitions injected into Chat Completions for ``-mcp`` model IDs.

## What ``-mcp`` variants are

Every cloud model in the catalog has a synthetic ``{id}-mcp`` twin
(e.g. ``openai/gpt-5.4-mcp``).  When a request targets such a model the
cloud proxy:

1. Strips the ``-mcp`` suffix and routes to the real upstream model.
2. Injects ``MCP_TOOL_DEFINITIONS`` into ``request["tools"]`` before
   forwarding.

The tool definitions mirror the curated Cortex + RAG tool surface exposed
by the MCP server (cortex entity lookup, assertions, deadlines, RAG search).

## Intended clients — agentic, NOT chat UIs

``-mcp`` variants are exclusively for **agentic clients** that implement the
tool-call execution loop:

    client → POST /v1/chat/completions (model=...mcp)
    ↓
    model responds with finish_reason="tool_calls"
    ↓
    client executes each tool_call against the MCP server / Cortex / RAG
    ↓
    client appends tool-result messages (role="tool")
    ↓
    client re-submits the conversation for the final model response

**Do NOT use ``-mcp`` variants in chat UIs** (OpenWebUI, Cursor chat,
plain curl one-shots, etc.).  Those clients do not run the tool execution
loop.  When the model issues a tool call the chat UI receives
``finish_reason="tool_calls"`` with no ``content``, which renders as an
empty or broken message and the conversation stalls.

Use the bare model ID (e.g. ``openai/gpt-5.4``) for interactive chat.
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
