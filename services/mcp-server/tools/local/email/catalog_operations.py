"""Catalog and availability operations for the email MCP tool.

Provides the runtime ``op="list"`` handler that returns the grouped catalog
and the ``not_yet`` fallback used for ops that are declared in the catalog
but do not yet have a concrete handler (or whose email-bridge endpoint is
still pending).

The catalog data lives in the sibling ``tools.local._email_catalog`` module
so that both the dispatch layer and future admin tooling can import it
without pulling in relay or handler logic.
"""

from __future__ import annotations

from typing import Any

from tools.local._email_catalog import CATALOG

from .surface_op_guard import current_mcp_surface, email_op_allowed_on_surface


def op_list(**_: object) -> dict[str, Any]:
    """Structured catalog: ops grouped by phase with tier and status."""
    surface = current_mcp_surface()
    by_phase: dict[int, dict[str, Any]] = {}
    for op, meta in CATALOG.items():
        if surface == "life" and not email_op_allowed_on_surface(op, surface=surface):
            continue
        phase = meta["phase"]
        if phase not in by_phase:
            by_phase[phase] = {}
        by_phase[phase][op] = {
            "tier": meta["tier"],
            "status": meta["status"],
            "desc": meta["desc"],
        }
    live = [
        op
        for op, m in CATALOG.items()
        if m["status"] == "live" and email_op_allowed_on_surface(op, surface=surface)
    ]
    payload: dict[str, Any] = {
        "live_ops": sorted(live),
        "total_ops": sum(len(ops) for ops in by_phase.values()),
        "phases": by_phase,
    }
    if surface == "life":
        payload["surface"] = "life"
        payload["surface_policy"] = "tier-R read ops only"
    return payload


def not_yet(op: str) -> dict[str, Any]:
    """Handler for ops whose email-bridge REST endpoint is not yet built."""
    meta = CATALOG.get(op, {})
    status = meta.get("status", "unknown")
    phase = meta.get("phase", "?")
    if status == "removed":
        return {"error": f"Op {op!r} was removed. {meta.get('desc', '')}".strip()}
    if status == "future":
        return {"error": f"Op {op!r} is planned for a future phase (phase {phase})."}
    return {
        "error": f"Op {op!r} needs a new email-bridge endpoint (phase {phase}). "
        "Use op='list' for current status."
    }
