"""Tests for /mcp/grok route (B2).

Four concerns:
  1. tools/list returns 65 tools with correct names
  2. Each tool's inputSchema matches canonical.yaml json_schema
  3. Round-trip dispatch via /mcp/grok returns a real result (read-only tool)
  4. /mcp route is unaffected (regression check)
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
CANONICAL_YAML = REPO_ROOT / "config" / "mcp" / "canonical.yaml"

sys.path.insert(0, str(MCP_SERVER_DIR))
os.environ.setdefault("MCP_AUTH_TOKEN", "test-noop")
os.environ.setdefault("MCP_OAUTH_DISABLED", "1")


@pytest.fixture(scope="module")
def grok_server_state() -> dict:
    """Build grok server once for all tests in this module."""
    from _derive import derive_grok_manifest  # noqa: PLC0415
    from grok_route import build_grok_server  # noqa: PLC0415
    from server import _build_server  # noqa: PLC0415

    _mcp, pre_prune_tool_objects, overflow_metadata, overflow_registry = _build_server()
    manifest = derive_grok_manifest(CANONICAL_YAML)
    grok_mcp = build_grok_server(
        pre_prune_tool_objects,
        overflow_metadata=overflow_metadata,
        overflow_registry=overflow_registry,
    )
    tools = asyncio.run(grok_mcp.list_tools())
    return {
        "grok_mcp": grok_mcp,
        "manifest": manifest,
        "tools": tools,
        "tool_names": {t.name for t in tools},
    }


@pytest.fixture(scope="module")
def main_server_state() -> dict:
    """Build main server once for regression checks."""
    from server import _build_server  # noqa: PLC0415

    mcp, _pre_prune, _overflow_md, _overflow_reg = _build_server()
    tools = asyncio.run(mcp.list_tools())
    return {
        "mcp": mcp,
        "tool_names": {t.name for t in tools},
        "tools": tools,
    }


# ── Test 1: tools/list count and names ────────────────────────────────────────


def test_grok_tools_list_count(grok_server_state: dict) -> None:
    """B2-T1: /mcp/grok/tools/list returns exactly as many tools as canonical.yaml."""
    manifest = grok_server_state["manifest"]
    tools = grok_server_state["tools"]
    assert len(tools) == len(manifest), (
        f"/mcp/grok registered {len(tools)} tools but canonical.yaml has "
        f"{len(manifest)} tools. Missing: "
        f"{sorted({e['name'] for e in manifest} - {t.name for t in tools})}"
    )


def test_grok_tools_list_names_match_canonical(grok_server_state: dict) -> None:
    """B2-T1: every tool name in /mcp/grok matches a canonical.yaml entry."""
    manifest = grok_server_state["manifest"]
    canonical_names = {e["name"] for e in manifest}
    tool_names = grok_server_state["tool_names"]

    extra = tool_names - canonical_names
    missing = canonical_names - tool_names
    assert not extra and not missing, (
        f"Name mismatch — extra: {sorted(extra)}, missing: {sorted(missing)}"
    )


# ── Test 2: inputSchema matches canonical.yaml json_schema ────────────────────


def test_grok_tool_schemas_match_canonical(grok_server_state: dict) -> None:
    """B2-T2: each tool's inputSchema matches canonical.yaml json_schema."""
    import yaml  # type: ignore[import]

    raw = yaml.safe_load(CANONICAL_YAML.read_text())
    schema_by_name: dict[str, dict] = {
        t["flat_call_shape"]["tool"]: t.get("json_schema", {})
        for t in raw.get("tools", [])
        if "mcp_grok" in t.get("seat_visibility", [])
    }

    grok_mcp = grok_server_state["grok_mcp"]

    async def _get_schemas() -> dict[str, dict]:
        result = {}
        for tool in await grok_mcp.list_tools():
            tool_obj = await grok_mcp.get_tool(tool.name)
            schema = (
                getattr(tool_obj, "parameters", None)
                or getattr(tool_obj, "inputSchema", None)
                or {}
            )
            if hasattr(schema, "model_dump"):
                schema = schema.model_dump(exclude_none=True, by_alias=True)
            result[tool.name] = schema
        return result

    registered_schemas = asyncio.run(_get_schemas())

    mismatches: list[str] = []
    for name, canonical_schema in schema_by_name.items():
        if name not in registered_schemas:
            mismatches.append(f"{name}: not registered")
            continue
        # Compare serialised to normalise field ordering
        canonical_json = json.dumps(canonical_schema, sort_keys=True)
        registered_json = json.dumps(registered_schemas[name], sort_keys=True)
        if canonical_json != registered_json:
            mismatches.append(
                f"{name}: canonical={canonical_json[:120]} "
                f"registered={registered_json[:120]}"
            )

    assert not mismatches, (
        f"{len(mismatches)} schema mismatches between /mcp/grok and canonical.yaml:\n"
        + "\n".join(mismatches[:10])
    )


# ── Test 3: round-trip dispatch via grok server ────────────────────────────────


def test_grok_round_trip_dispatch_retrieve(grok_server_state: dict) -> None:
    """B2-T3: calling a real read-only tool via grok_mcp returns a real result.

    Uses cortex_entity_get (safe, read-only) to verify the tool is callable.
    """
    grok_mcp = grok_server_state["grok_mcp"]
    tool_names = grok_server_state["tool_names"]

    # Use cortex_entity_get if available; fall back to tool_search (always registered)
    if "cortex_entity_get" in tool_names:
        target_tool = "cortex_entity_get"
        args = {"entity_id": "nonexistent:B2-smoke-test"}
    elif "tool_search" in tool_names:
        target_tool = "tool_search"
        args = {"query": "filesystem read operations"}
    else:
        pytest.skip("Neither cortex_entity_get nor tool_search found in grok tools")
        return

    async def _call() -> dict:
        result = await grok_mcp.call_tool(target_tool, args)
        return result

    result = asyncio.run(_call())
    # A real result (even an error response) proves the callpath is wired.
    # We just need a non-exception response — errors like "not found" are valid.
    assert result is not None, f"grok call to {target_tool} returned None"


# ── Test 4: /mcp regression — main server unaffected by B2 ────────────────────


def test_main_server_unaffected_by_b2(
    main_server_state: dict,
) -> None:
    """B2-T4: main /mcp server still has exactly the primary tool set after B2."""
    from server import _PRIMARY_TOOLS  # noqa: PLC0415

    tool_names = main_server_state["tool_names"]
    # All primary tools must still be present
    for t in _PRIMARY_TOOLS:
        assert t in tool_names, f"Primary tool {t!r} missing from /mcp after B2 changes"
    # Grok tools (individual named tools) must NOT be in main server
    # (they should stay in overflow, not promoted to primary)
    grok_individual = {
        "cortex_entities",
        "cortex_entity_get",
        "agent_bus_fetch",
        "fs_read",
        "grokbuild_build",
    }
    for t in grok_individual:
        assert t not in tool_names, (
            f"Individual tool {t!r} leaked into /mcp primary set — B2 regression"
        )


# ── Test 5: verify_grok_manifest_count raises on mismatch ─────────────────────


def test_verify_grok_manifest_count_raises_on_mismatch() -> None:
    """B2-T5: verify_grok_manifest_count raises RuntimeError when count differs."""
    from fastmcp import FastMCP  # noqa: PLC0415
    from grok_route import verify_grok_manifest_count  # noqa: PLC0415

    # Build a FastMCP with zero tools — should trigger RuntimeError
    empty_mcp = FastMCP("empty-grok")

    with pytest.raises(RuntimeError, match="Grok manifest count mismatch"):
        verify_grok_manifest_count(empty_mcp)
