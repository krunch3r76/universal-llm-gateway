"""Tool-call schemas for agent-seat tool loops.

OpenAI function-calling definitions shared between the MCP ``frontier_generate``
path and the pipeline ``frontier_dispatch_v1`` handler. Both surfaces use the
same OpenAI-shape tool schema — providers that speak native Anthropic / xAI /
Google formats are translated by upstream adapters (MCP's ``llm_adapters``;
Stargate's cloud-proxy).

Two tiers:

- ``TOOL_DEFINITIONS`` — cortex (dispatch) + RAG. Read-heavy workloads.
- ``TEAM_TOOL_DEFINITIONS`` — cortex + agent_bus (write access + inter-agent
  messaging). Superset of the read tier.

``RAG_SEARCH_TOOL_DEFINITION`` is the single RAG entry reused in both tiers.
The cortex op registry lives in cortex-api; both tiers share the same tool
schema here and the same op space at the /dispatch endpoint.
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


CORTEX_TOOL_DEFINITION: dict[str, Any] = _fn(
    "cortex",
    "Cortex knowledge system — unified dispatch tool for the full Cortex "
    "surface.\n\n"
    "Key operations:\n"
    "  entities (type?, workflow_state?, limit?)  entity_get (entity_id)\n"
    "  entity_create (id, type, name, ...)  entity_update (entity_id, ...)\n"
    "  assertions (entity_id?, confidence?, limit?)\n"
    "  assert (entity_id, claim, confidence, evidence, derivation_type, ...)\n"
    "  observe (claim, entity_id?, agent?)  — lightweight observation\n"
    "  supersede (old_assertion_id, entity_id, claim, confidence, evidence, "
    "session_id, agent)\n"
    "  search (query, limit?, entity_type?)  deadlines ()\n"
    "  journal_read (limit?)  journal_write (timestamp, agent, summary, ...)\n"
    "  session_close (session_id, agent, transcript_md, summary, ...)\n"
    "  edge_create (session_id, agent, from_node, to_node, edge_type, ...)\n"
    "  edges (from_node?, to_node?, edge_type?, limit?)\n"
    "  edge_traverse (node, hops?, edge_type?)\n"
    "  review_queue (limit?)  activate (entity_ids, depth?, max_results?)\n"
    "  analyze_impact (entity_id, claim, confidence?)\n"
    "  resolve (uri, tag?)  tag_assign (tag_name, entity_id, assertion_id, agent)\n"
    "  tag_list (entity_id)  tag_resolve (tag_name, entity_id)  impact (entity_id, depth?)\n"
    "  relationships (entity_id?, type_id?, limit?)\n"
    "  relationship_create (source_id, target_id, type_id, ...)\n"
    "  stats ()  surface_forms (entity_id?, mention?, mention_type?, limit?)\n"
    "  ingest_document (source_uri, content, observer?, source_date?)\n"
    "  assert_from_chunk (chunk_id, entity_id, claim, confidence, evidence, ...)\n"
    "  friction (service, category, note, suggestion?, agent?)\n"
    "  rj_write (agent, register, entry, kind?, ...)\n"
    "  rj_read (entry_id)  rj_list (agent?, kind?, limit?, offset?)\n"
    "  rj_link (entry_id, to_entry?, to_entity?, link_type?)\n"
    "  rj_consolidate (agent, register, entry, throughline, before, now, ...)\n\n"
    "confidence: confirmed / believed / suspected / hypothesized\n"
    "arguments MUST be a JSON string or an object.",
    {
        "tool": {
            "type": "string",
            "description": (
                "Operation name (e.g. entity_get, assert, observe, "
                "search, journal_write, session_close, edge_create)"
            ),
        },
        "arguments": {
            "type": "string",
            "description": (
                "JSON string (or object) of operation arguments. "
                'Example: \'{"entity_id": "person:jane-doe"}\''
            ),
        },
    },
    ["tool"],
)


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    CORTEX_TOOL_DEFINITION,
    RAG_SEARCH_TOOL_DEFINITION,
]


TEAM_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    CORTEX_TOOL_DEFINITION,
    _fn(
        "agent_bus",
        "Inter-agent message bus — threads, turns, read/reply coordination.\n\n"
        "Operations:\n"
        "  fetch   (thread, last?, compact?, mark_read?) — get turns\n"
        "  reply   (thread, to, subject, body, after_turn, from_agent?) — reply\n"
        "  post    (slug, to, subject, body, from_agent?) — new thread\n"
        "  threads (status?) — list threads; status: active/archived/all\n"
        "  get     (thread, turn_number) — single turn lookup\n\n"
        "arguments MUST be a JSON string or an object.",
        {
            "tool": {
                "type": "string",
                "description": "Operation: fetch, reply, post, threads, get",
            },
            "arguments": {
                "type": "string",
                "description": (
                    "JSON string (or object) of operation arguments. "
                    'Example: \'{"thread": "480", "last": 3, "compact": true}\''
                ),
            },
        },
        ["tool"],
    ),
]


def _tool_name(definition: dict[str, Any]) -> str:
    """Extract function name from an OpenAI tool definition."""
    fn = definition.get("function")
    if not isinstance(fn, dict):
        return ""
    name = fn.get("name")
    return str(name) if isinstance(name, str) else ""


TOOL_REGISTRY: dict[str, dict[str, Any]] = {}
for _definition in [*TOOL_DEFINITIONS, *TEAM_TOOL_DEFINITIONS]:
    _name = _tool_name(_definition)
    if _name:
        TOOL_REGISTRY[_name] = _definition


def resolve_tools(tool_names: list[str]) -> list[dict[str, Any]]:
    """Resolve ordered tool names to OpenAI function definitions."""
    resolved: list[dict[str, Any]] = []
    unknown: list[str] = []
    for name in tool_names:
        definition = TOOL_REGISTRY.get(name)
        if definition is None:
            unknown.append(name)
            continue
        resolved.append(definition)
    if unknown:
        raise ValueError(
            f"Unknown tool name(s): {sorted(set(unknown))}; "
            f"available: {sorted(TOOL_REGISTRY)}"
        )
    return resolved
