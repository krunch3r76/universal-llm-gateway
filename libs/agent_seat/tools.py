"""Tool-call schemas for agent-seat tool loops.

OpenAI function-calling definitions shared between the MCP ``team_dispatch``
relay and the pipeline ``frontier_dispatch_v1`` handler. Both surfaces use the
same OpenAI-shape tool schema — providers that speak native Anthropic / xAI /
Google formats are translated by upstream adapters (MCP's ``llm_adapters``;
Stargate's cloud-proxy).

Two tiers:

- ``TOOL_DEFINITIONS`` — static cortex dispatch fallback for read-heavy workloads.
- ``TEAM_TOOL_DEFINITIONS`` — cortex + agent_bus (write access + inter-agent
  messaging). Superset of the read tier.

RAG is sourced from the live MCP ``rag`` descriptor, not a local shim.
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
    "  session_close (session_id, agent, {transcript_jsonl_path | transcript_md}, session_summary_md, summary, domains?, decisions?, open_items?, entity_ids?, prior_session_id?, handoff_prompt?, assistant_label?, dry_run?)\n"
    "    — atomic server-side close. EITHER-OF the transcript source: cursor passes\n"
    "    transcript_jsonl_path (server reads the JSONL under CURSOR_AGENT_TRANSCRIPTS_ROOT\n"
    "    and assembles the verbatim layer); web passes transcript_md directly (used as the\n"
    "    verbatim layer as-is). If both supplied, jsonl_path wins. Server appends the\n"
    "    agent-composed session_summary_md (must start with '## Session Summary'), and\n"
    "    atomically writes file + entity + journal row + continues edge. handoff_prompt,\n"
    "    when supplied, is persisted on the session_journals row. Response includes\n"
    "    transcript_entity_id, content_hash (sha256:<hex> — quote as provenance), turn_count,\n"
    "    byte_count, and journal_row_id.\n"
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


# Safe alias for the Brave Search MCP tool. The MCP tool is named
# "web_search" at the server layer, but that name collides with
# Claude's and Gemini's native search capability when injected into
# frontier model tool lists.  Callers MUST use "brave_search" — the
# executor translates the call to "web_search" on the MCP side.
BRAVE_SEARCH_TOOL_DEFINITION: dict[str, Any] = _fn(
    "brave_search",
    "Live web search via the Brave Search API. Returns current search "
    "results for the given query. Use this for real-time lookups — "
    "prices, news, recent events, URLs. ALWAYS use this tool, never "
    "the model's native web_search capability.",
    {
        "query": {"type": "string", "description": "Search query"},
        "max_results": {
            "type": "integer",
            "description": "Max results to return (default 5, max 10)",
        },
    },
    ["query"],
)


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    CORTEX_TOOL_DEFINITION,
]


_AGENT_BUS_TOOL_DEFINITION: dict[str, Any] = _fn(
    "agent_bus",
    "Inter-agent message bus — threads, turns, read/reply coordination.\n\n"
    "Body convention: keep post/reply bodies brief. Long handoffs, specs, "
    "reviews, and analysis must be written first as Cortex sidecars under "
    "notes/system/threads/... via fs(sandbox='cortex'), then referenced as "
    "cortex:notes/system/threads/<file>. Workspace packets are mirrors, not "
    "the primary bus artifact.\n\n"
    "Operations:\n"
    "  fetch   (thread, last?, compact?, mark_read?) — get turns\n"
    "  reply   (thread, to, subject, body, after_turn, from?) — reply; from\n"
    "          defaults to the dispatched role when omitted in tool loops\n"
    "  post    (slug, to, subject, body, from?) — new thread; same default\n"
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
)


TEAM_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    CORTEX_TOOL_DEFINITION,
    _AGENT_BUS_TOOL_DEFINITION,
]

# Static fallback when the live MCP catalog is unreachable. TEAM_TOOL_DEFINITIONS
# is the superset — do NOT concatenate TOOL_DEFINITIONS (duplicate cortex).
STATIC_TOOL_FALLBACK: list[dict[str, Any]] = TEAM_TOOL_DEFINITIONS


# Tool-registry entry: definition + async executor reference name. The
# executor name is resolved by libs/agent_seat/executor.py at tool-loop
# build time — keeps this module free of concrete executor imports
# (avoids agent_seat → executor → tools cycle).
TOOL_REGISTRY: dict[str, dict[str, Any]] = {
    "cortex": {
        "definition": CORTEX_TOOL_DEFINITION,
        "executor": "cortex_dispatch",
    },
    "agent_bus": {
        "definition": _AGENT_BUS_TOOL_DEFINITION,
        "executor": "agent_bus_dispatch",
    },
    # Safe alias — executor remaps to MCP "web_search" (see executor.py).
    # ¬use "web_search" directly in frontier dispatches: collides with
    # Claude's and Gemini's native search tool name.
    "brave_search": {
        "definition": BRAVE_SEARCH_TOOL_DEFINITION,
        "executor": "brave_search",
    },
}


def resolve_tools(
    names: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Resolve tool names to ``(definitions, executor_names)``.

    Raises ``ValueError`` for unknown names — callers should validate
    against ``TOOL_REGISTRY`` before calling.
    """
    definitions: list[dict[str, Any]] = []
    executors: list[str] = []
    unknown: list[str] = []
    for name in names:
        entry = TOOL_REGISTRY.get(name)
        if entry is None:
            unknown.append(name)
            continue
        definitions.append(entry["definition"])
        executors.append(entry["executor"])
    if unknown:
        raise ValueError(
            f"unknown tool {sorted(set(unknown))!r}; available: {sorted(TOOL_REGISTRY)}"
        )
    return definitions, executors
