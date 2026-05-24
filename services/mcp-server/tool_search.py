"""Tool Search — runtime discovery for dispatched (non-primary) tools.

The MCP catalog advertises a compact primary set (≤24 domain dispatchers from
``config/mcp/canonical.yaml`` via ``_derive.get_claude_manifest``): ``agent_bus``,
``cortex``, ``dispatch``, ``fs``, ``grokbuild``, ``manage``, ``observability``,
``pipeline``, ``rag``, ``retrieve``, ``tool_search``. All other tools registered
at boot — ``cortex_boot``, ``sql``, ``web_fetch``, ``quality_gate``, etc. — are
pruned from ``tools/list`` but kept in the overflow registry. Gitignored
``tools.local`` surfaces (e.g. ``email`` → email-bridge UDS relay) follow the
same path when present: ``tool_search`` then ``dispatch(tool="email", ...)``.
Domain tools such as ``email`` expose their op catalog at runtime
(``op="list"``); those catalogs are intentionally omitted from boot prompts.

``tool_search`` returns the metadata needed for a valid dispatch call: name,
purpose, ops, required-args-by-op, dispatch_template, example.

The manifest is built once at server startup from tool descriptions captured
*before* ``_prune_to_primary`` removes non-primary Tool objects. Keys are sorted
so ``tools/list`` and the manifest are byte-deterministic across boots (Anthropic
prompt-cache invariant).

Module split (post-SLOC-gate, per master diff review):
  - ``tool_search_matcher`` — parser/scorer primitives, ``ManifestEntry`` use
    only via TYPE_CHECKING.
  - ``tool_search_manifest`` — ``ManifestEntry``, ``build_manifest_from_metadata``,
    ``build_manifest``, ``capture_overflow_metadata``.
  - ``tool_search`` (this module) — FastMCP registration, ``_MANIFEST`` cache.
Public API (``capture_overflow_metadata``, ``register_tool_search_tool``,
``build_manifest_from_metadata``, ``ManifestEntry``) re-exported here for
backward compatibility with the previous flat module.
"""

from __future__ import annotations

from typing import Any

from _derive import get_claude_manifest
from fastmcp import FastMCP
from mcp_events import record
from tool_search_manifest import (
    ManifestEntry,
    build_manifest,
    build_manifest_from_metadata,
    capture_overflow_metadata,
)
from tool_search_matcher import (
    _all_manifest_summary,
    _entry_to_response,
    search_manifest,
)

PRIMARY_TOOLS_FROZEN: frozenset[str] = frozenset(
    e["tool_name"] for e in get_claude_manifest()
)

_MANIFEST: dict[str, ManifestEntry] = {}


__all__ = [
    "ManifestEntry",
    "PRIMARY_TOOLS_FROZEN",
    "build_manifest",
    "build_manifest_from_metadata",
    "capture_overflow_metadata",
    "register_tool_search_tool",
    "search_manifest",
]


def register_tool_search_tool(
    mcp: FastMCP,
    overflow_metadata: dict[str, tuple[str, dict[str, Any]]],
) -> None:
    """Build the manifest from pre-captured metadata and register ``tool_search``.

    ``overflow_metadata`` MUST be captured before ``_prune_to_primary`` runs —
    the tools removed by pruning would otherwise return empty descriptions.
    """
    global _MANIFEST
    _MANIFEST = build_manifest_from_metadata(overflow_metadata)

    @mcp.tool(title="Tool Search (Discovery)")
    def tool_search(query: str, limit: int = 5) -> dict[str, Any]:
        """Search for MCP tools not in your primary catalog.

        Primary (in ``tools/list``): agent_bus, cortex, dispatch, fs, grokbuild,
        manage, observability, pipeline, rag, retrieve, tool_search. Overflow
        examples — session boot (cortex_boot, boot_inspect), data (sql,
        web_fetch, web_search), codegen (quality_gate), frontier/team dispatch
        surfaces, pipeline_consult, and ``tools.local`` domains (email →
        email-bridge) when installed — use this search, then ``dispatch(...)``.

        Pass keywords matching what you want to do; results include ready-to-paste
        dispatch calls. Examples:
          tool_search(query="cortex boot")
          tool_search(query="email mailbox")
          tool_search(query="raw sql")
        """
        record("mcp.tool.search.called", query=query, limit=limit)
        if not query or not query.strip():
            record("mcp.tool.search.empty")
            return {
                "query": query,
                "results": [],
                "total_matches": 0,
                "available_tools_summary": _all_manifest_summary(_MANIFEST),
                "_next": (
                    "Empty query. See available_tools_summary for the full "
                    "list; pass keywords matching the operation you want."
                ),
            }
        results = search_manifest(_MANIFEST, query, limit=limit)
        if not results:
            record("mcp.tool.search.miss", query=query)
            return {
                "query": query,
                "results": [],
                "total_matches": 0,
                "available_tools_summary": _all_manifest_summary(_MANIFEST),
                "_next": (
                    "No matches. See available_tools_summary above; refine "
                    "query keywords."
                ),
            }
        return {
            "query": query,
            "results": [_entry_to_response(e) for e in results],
            "total_matches": len(results),
            "_next": (
                "Call dispatch with the template — do not re-search unless "
                "the result is clearly wrong."
            ),
        }
