"""Tool surface for the multi-model agent loop.

Defines the OpenAI function-calling tool schemas and the executor that
dispatches tool calls to local Cortex + RAG REST endpoints.
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

Entity IDs use type:slug format: person:kaywan-mansubi, account:chase-credit_card-0780, \
legal_matter:osaic-demand, decision:finance-pipeline-phase3.

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
                "description": "Entity ID (e.g. person:kaywan-mansubi)",
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
