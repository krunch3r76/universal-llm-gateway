"""Tool Search — runtime discovery for dispatched (non-primary) tools.

The MCP catalog is intentionally lean: only ``{cortex, agent_bus, fs,
dispatch, tool_search, retrieve}`` are advertised as primary tools. Everything
else is reachable via ``dispatch(tool="...", arguments='...')``. ``tool_search``
returns the metadata an agent needs to construct a valid dispatch call:
name, purpose, ops, required-args-by-op, dispatch_template, example.

The manifest is built once at server startup from the overflow_registry
produced by ``_prune_to_primary``. Keys are inserted in sorted order so the
``tools/list`` rendering and the manifest itself are byte-deterministic
across boots — required for the Anthropic prompt-cache invariant.

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
    {"cortex", "agent_bus", "fs", "dispatch", "tool_search", "retrieve"}
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

        The primary catalog (cortex, agent_bus, fs, dispatch, retrieve,
        tool_search) is intentionally lean. Most other tools — pipelines,
        dispatch surfaces (team/frontier/grok), service control (manage),
        observability/debugging (events, traces), data (sql, rag, web_fetch,
        web_search), session boot (cortex_boot, boot_inspect), code quality
        (quality_gate) — require this search + ``dispatch(...)``.

        Pass keywords matching what you want to do; results include ready-to-paste
        dispatch calls. Examples:
          tool_search(query="restart service")
          tool_search(query="poll pipeline")
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
