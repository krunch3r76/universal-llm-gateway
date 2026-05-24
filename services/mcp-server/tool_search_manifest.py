"""Tool-search manifest construction — pre-prune metadata capture + entry building.

Lifted out of ``tool_search.py`` to keep that module under the 300-line SLOC
budget. ``ManifestEntry`` is the wire-facing dataclass; the build pipeline
runs ``capture_overflow_metadata`` BEFORE ``_prune_to_primary`` removes
non-primary tools, then ``build_manifest_from_metadata`` materialises the
manifest. Async ``build_manifest`` is a test-only convenience.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from fastmcp import FastMCP
from mcp_events import record
from tool_search_matcher import (
    _derive_keywords,
    _extract_example,
    _extract_ops_and_required_args,
    _first_sentence,
    _render_dispatch_template,
)
from universal_logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class ManifestEntry:
    name: str
    purpose: str
    dispatch_template: str
    ops: list[str] = field(default_factory=list)
    required_args_by_op: dict[str, list[str]] = field(default_factory=dict)
    example: str = ""
    keywords: tuple[str, ...] = ()


def build_manifest_from_metadata(
    overflow_metadata: dict[str, tuple[str, dict[str, Any]]],
) -> dict[str, ManifestEntry]:
    """Build a manifest from pre-captured ``{name: (description, schema)}`` pairs.

    This synchronous form is used in production because tool descriptions must
    be captured BEFORE ``_prune_to_primary`` removes the underlying tool objects.
    """
    manifest: dict[str, ManifestEntry] = {}
    for name in sorted(overflow_metadata):
        description, schema = overflow_metadata[name]
        ops, req = _extract_ops_and_required_args(description, schema)
        # Prefer live schema properties over description parsing to avoid staleness
        # after parameter renames (e.g. from -> from_agent in agent_bus tools).
        props = (schema or {}).get("properties", {}) or {}
        if props:
            schema_req = (schema or {}).get("required", []) or []
            req = {op: [k for k in props if k in schema_req] for op in (ops or ["default"])}
        manifest[name] = ManifestEntry(
            name=name,
            purpose=_first_sentence(description),
            ops=ops,
            dispatch_template=_render_dispatch_template(name, schema, ops),
            required_args_by_op=req,
            example=_extract_example(description),
            keywords=_derive_keywords(name, description),
        )
    return manifest


async def build_manifest(
    mcp: FastMCP, overflow_registry: dict[str, Callable[..., Any]]
) -> dict[str, ManifestEntry]:
    """Build a manifest by reading tool metadata from a live ``FastMCP`` server.

    Test-only convenience wrapper. In production, descriptions must be captured
    before ``_prune_to_primary`` removes the tools — see
    ``build_manifest_from_metadata`` and ``capture_overflow_metadata``.
    """
    metadata: dict[str, tuple[str, dict[str, Any]]] = {}
    for name in sorted(overflow_registry):
        try:
            tool_obj = await mcp.get_tool(name)
        except Exception as exc:
            logger.warning("build_manifest skipped %s: %s", name, exc)
            record(
                "mcp.tool_search.build_manifest.skipped",
                tool=name,
                error=str(exc),
            )
            continue
        description = getattr(tool_obj, "description", "") or ""
        schema = (
            getattr(tool_obj, "parameters", None)
            or getattr(tool_obj, "inputSchema", None)
            or {}
        )
        metadata[name] = (description, schema)
    return build_manifest_from_metadata(metadata)


async def capture_overflow_metadata(
    mcp: FastMCP, primary_names: set[str] | frozenset[str]
) -> dict[str, tuple[str, dict[str, Any]]]:
    """Snapshot description+schema for every non-primary tool before pruning."""
    metadata: dict[str, tuple[str, dict[str, Any]]] = {}
    for tool in await mcp.list_tools():
        if tool.name in primary_names:
            continue
        try:
            tool_obj = await mcp.get_tool(tool.name)
        except Exception as exc:
            logger.warning(
                "capture_overflow_metadata skipped %s: %s", tool.name, exc
            )
            record(
                "mcp.tool_search.capture.failed",
                tool=tool.name,
                error=str(exc),
            )
            continue
        description = getattr(tool_obj, "description", "") or ""
        schema = (
            getattr(tool_obj, "parameters", None)
            or getattr(tool_obj, "inputSchema", None)
            or {}
        )
        metadata[tool.name] = (description, schema)
    return metadata
