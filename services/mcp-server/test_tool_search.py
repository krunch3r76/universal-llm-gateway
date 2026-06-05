"""Tool-search manifest, ranking, and catalog wire-size tests.

Three concerns in one module:
  1. Manifest coverage — every overflow tool has a manifest entry; no primary
     tool leaks into it.
  2. Search-ranking quality (golden test) — natural-language queries map to
     the expected top-1 tool.
  3. Wire-size regression — total ``tools/list`` bytes ≤ baseline; render
     twice and assert byte-identical (prompt-cache invariant).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MCP_SERVER_DIR = REPO_ROOT / "services" / "mcp-server"
BASELINE_PATH = MCP_SERVER_DIR / "test_tool_catalog_baseline.txt"

sys.path.insert(0, str(MCP_SERVER_DIR))
os.environ.setdefault("MCP_AUTH_TOKEN", "test-noop")
os.environ.setdefault("MCP_OAUTH_DISABLED", "1")


@pytest.fixture(scope="module")
def server_state() -> dict:
    """Build the server once; manifest is set by register_tool_search_tool at boot."""
    import tool_search as ts_module  # noqa: PLC0415
    from server import _PRIMARY_TOOLS, _build_server  # noqa: PLC0415

    mcp, _pre_prune, _overflow_md, _overflow_reg = _build_server()
    tools = asyncio.run(mcp.list_tools())
    manifest = dict(ts_module._MANIFEST)
    return {
        "primary": set(_PRIMARY_TOOLS),
        "manifest": manifest,
        "tool_records": [
            t.to_mcp_tool().model_dump(exclude_none=True, by_alias=True)
            if hasattr(t, "to_mcp_tool")
            else t.model_dump(exclude_none=True, by_alias=True)
            for t in tools
        ],
    }


# Phase D promotes pipeline/rag/observability/manage to primary (13-domain catalog
# after grokbuild demotion 2026-06-02); tool_search manifest covers overflow/demoted tools only.
# frontier_dispatch/team_dispatch promoted to PRIMARY via standalone domains
# (thread 1146/1167) — no longer in the overflow manifest.
_EXPECTED_DEMOTED = {
    "web_fetch",
    "boot_inspect",
    "model_status",
    "sql",
    "web_search",
    "quality_gate",
}


def test_manifest_covers_every_demoted_tool(server_state: dict) -> None:
    manifest_keys = set(server_state["manifest"])
    missing = _EXPECTED_DEMOTED - manifest_keys
    assert not missing, f"manifest missing entries for: {sorted(missing)}"


def test_manifest_excludes_primary_tools(server_state: dict) -> None:
    primary = server_state["primary"]
    manifest_keys = set(server_state["manifest"])
    leaked = primary & manifest_keys
    assert not leaked, f"primary tools leaked into manifest: {sorted(leaked)}"


def test_manifest_entries_have_required_fields(server_state: dict) -> None:
    for name, entry in server_state["manifest"].items():
        assert entry.name == name
        assert entry.purpose, f"{name}: empty purpose"
        assert entry.dispatch_template, f"{name}: empty dispatch_template"
        assert entry.dispatch_template.startswith(f'dispatch(tool="{name}"'), (
            f"{name}: dispatch_template missing tool name binding"
        )


GOLDEN_QUERIES: list[tuple[str, str]] = [
    # Golden pairs target overflow flat tools. frontier_dispatch/team_dispatch
    # promoted to primary (thread 1146/1167) → no longer in the overflow manifest,
    # so their former entries are removed.
    ("restart service", "bot_supervisor"),
    ("fetch web page", "web_fetch"),
    ("raw sql query", "sql"),
    (
        "build with grok",
        "grokbuild",
    ),  # overflow vestigial relay (11588); prefer cursorbuild
    ("rag semantic search query", "rag_search"),
    ("model status", "model_status"),
    ("query events", "query_observability_preview"),
    ("pipeline consult", "pipeline_consult"),
]


def test_cortex_boot_is_primary_not_overflow(server_state: dict) -> None:
    """cortex_boot promoted to primary 2026-06-02 — not in overflow manifest."""
    assert "cortex_boot" in server_state["primary"]
    assert "cortex_boot" not in server_state["manifest"]


@pytest.mark.parametrize("query,expected_top", GOLDEN_QUERIES)
def test_search_ranking_top_one(
    server_state: dict, query: str, expected_top: str
) -> None:
    from tool_search import search_manifest  # noqa: PLC0415

    results = search_manifest(server_state["manifest"], query, limit=5)
    assert results, f"no results for {query!r}"
    assert results[0].name == expected_top, (
        f"query {query!r}: expected top {expected_top}, got "
        f"{[r.name for r in results[:3]]}"
    )


def test_catalog_total_bytes_within_baseline(server_state: dict) -> None:
    """Wire size of advertised tools/list must stay at or below the locked baseline."""
    total = sum(
        len(json.dumps(r, separators=(",", ":"), default=str).encode("utf-8"))
        for r in server_state["tool_records"]
    )
    if BASELINE_PATH.exists():
        baseline = int(BASELINE_PATH.read_text().strip())
    else:
        baseline = 30000
    assert total <= baseline, (
        f"catalog wire size regressed: {total} B > baseline {baseline} B "
        f"(update {BASELINE_PATH.name} only on intentional growth)"
    )


def test_catalog_byte_deterministic_across_renders() -> None:
    """Render twice; canonical JSON must be byte-identical (prompt-cache invariant)."""
    from server import _build_server  # noqa: PLC0415

    def _render() -> str:
        mcp, _pre_prune, _overflow_md, _overflow_reg = _build_server()
        tools = asyncio.run(mcp.list_tools())
        recs = [
            t.to_mcp_tool().model_dump(exclude_none=True, by_alias=True)
            if hasattr(t, "to_mcp_tool")
            else t.model_dump(exclude_none=True, by_alias=True)
            for t in tools
        ]
        return json.dumps(recs, sort_keys=True, separators=(",", ":"), default=str)

    a, b = _render(), _render()
    assert a == b, "catalog rendering is non-deterministic across boots"
