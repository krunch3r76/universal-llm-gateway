"""Tool Search — runtime discovery for dispatched (non-primary) tools.

The MCP catalog advertises a compact primary set (≤24 domain dispatchers from
``config/mcp/canonical.yaml`` via ``_derive.get_claude_manifest``): ``agent_bus``,
``cortex``, ``dispatch``, ``fs``, ``manage``, ``observability``,
``pipeline``, ``rag``, ``retrieve``, ``tool_search``. All other tools registered
at session start — ``cortex_brief``, ``sql``, ``web_fetch``, ``quality_gate``, etc. — are
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
from endpoint_surface import Surface, filter_overflow_metadata_for_surface
from fastmcp import FastMCP
from mcp.types import ToolAnnotations
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
    primary_tool_hint_for_search,
    search_manifest,
    server_primary_empty_search_note,
)
from tool_search_primary_ops import (
    primary_op_to_response,
    search_primary_ops,
    verb_named_in_query,
)

PRIMARY_TOOLS_FROZEN: frozenset[str] = frozenset(
    e["tool_name"] for e in get_claude_manifest()
)

_MANIFEST: dict[str, ManifestEntry] = {}


def execute_tool_search(
    query: str,
    limit: int = 5,
    *,
    manifest: dict[str, ManifestEntry] | None = None,
) -> dict[str, Any]:
    """Core tool_search response builder."""
    active_manifest = manifest if manifest is not None else _MANIFEST
    record("mcp.tool.search.called", query=query, limit=limit)
    if not query or not query.strip():
        record("mcp.tool.search.empty")
        primary_note = server_primary_empty_search_note()
        return {
            "query": query,
            "results": [],
            "total_matches": 0,
            "available_tools_summary": _all_manifest_summary(active_manifest),
            "server_primary_note": primary_note,
            "_next": (
                "Empty query. See available_tools_summary for overflow tools; "
                "pass keywords matching the operation you want. "
                f"{primary_note}"
            ),
        }
    results = search_manifest(active_manifest, query, limit=limit)
    op_hits = search_primary_ops(query, limit=limit)
    if not results and not op_hits:
        record("mcp.tool.search.miss", query=query)
        primary_note = server_primary_empty_search_note()
        primary_hint = primary_tool_hint_for_search(query, results)
        payload: dict[str, Any] = {
            "query": query,
            "results": [],
            "total_matches": 0,
            "available_tools_summary": _all_manifest_summary(active_manifest),
            "server_primary_note": primary_note,
        }
        if primary_hint:
            payload["primary_tool_hint"] = primary_hint
            payload["_next"] = (
                "Use primary_tool_hint — call the named primary tool directly. "
                "Overflow index returned 0 matches for this query; that is expected "
                "for deferred server-primary names."
            )
        else:
            payload["_next"] = (
                "No overflow matches. See available_tools_summary above; refine "
                f"query keywords. {primary_note}"
            )
        return payload
    primary_hint = primary_tool_hint_for_search(query, results)
    overflow_rows = [_entry_to_response(e) for e in results]
    op_rows = [primary_op_to_response(e) for e in op_hits]
    # A query that names the verb ("friction", "assert") wants the call shape
    # first; otherwise sub-op hits trail the overflow tools they merely resemble.
    merged = (
        op_rows + overflow_rows
        if verb_named_in_query(query, op_hits)
        else overflow_rows + op_rows
    )
    payload: dict[str, Any] = {
        "query": query,
        "results": merged[:limit],
        "total_matches": len(merged),
    }
    if primary_hint:
        payload["primary_tool_hint"] = primary_hint
    if op_hits and (not results or verb_named_in_query(query, op_hits)):
        payload["_next"] = (
            "Use call_shape on the primary_op result — call the primary tool "
            "directly by name. Overflow dispatch templates (if any) are secondary."
        )
    elif primary_hint:
        payload["_next"] = (
            "Use primary_tool_hint — call the named primary tool directly; "
            "overflow dispatch templates below are secondary."
        )
    else:
        payload["_next"] = (
            "Call dispatch with the template — do not re-search unless "
            "the result is clearly wrong."
        )
    return payload


__all__ = [
    "ManifestEntry",
    "PRIMARY_TOOLS_FROZEN",
    "build_manifest",
    "build_manifest_from_metadata",
    "capture_overflow_metadata",
    "execute_tool_search",
    "register_tool_search_tool",
    "search_manifest",
]


def register_tool_search_tool(
    mcp: FastMCP,
    overflow_metadata: dict[str, tuple[str, dict[str, Any]]],
    *,
    surface: Surface = "code",
) -> None:
    """Build the manifest from pre-captured metadata and register ``tool_search``.

    ``overflow_metadata`` MUST be captured before ``_prune_to_primary`` runs —
    the tools removed by pruning would otherwise return empty descriptions.
    """
    global _MANIFEST
    filtered = filter_overflow_metadata_for_surface(overflow_metadata, surface)
    manifest = build_manifest_from_metadata(filtered)

    @mcp.tool(
        title="Tool Search (Discovery)",
        annotations=ToolAnnotations(readOnlyHint=True),
    )
    def tool_search(query: str, limit: int = 5) -> dict[str, Any]:
        """Search the overflow tool catalog (tools pruned from ``tools/list``).

        Server-primary tools (boot manifest line) are advertised via
        ``tools/list``. They may be **pre-bound** (direct callable) or
        **deferred** (one connector load hop, then call by name) — see the boot
        binding block. Absent from the initial callable set ≠ connector dropped
        the tool.

        Indexes **overflow tools** (sql, web_fetch, quality_gate, git_*,
        ``tools.local``) → ``dispatch_template`` for ``dispatch(tool=…)``, plus
        **named sub-ops of umbrella primaries** (``cortex.friction``,
        ``cortex.assert``, ``cortex.entity_create``, ``rag.search``,
        ``fs.md_read``) → ``kind="primary_op"`` with ``call_shape``: call that
        primary directly. Never route primaries through ``dispatch``. Whole
        primary tools are not indexed — see ``server_primary_note``.

        Pass keywords; default limit=5. Examples:
          tool_search(query="raw sql")
          tool_search(query="friction")
        """
        return execute_tool_search(query, limit=limit, manifest=manifest)

    _MANIFEST = manifest
