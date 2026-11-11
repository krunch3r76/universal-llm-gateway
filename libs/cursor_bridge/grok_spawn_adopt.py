"""Adopt an already-created Grok agent from roster by unique name match."""

from __future__ import annotations

from typing import Any


async def adopt_agent_by_name(
    display: str | int,
    name: str,
    *,
    pre_create_ids: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Adopt a roster row whose id was not in the pre-create snapshot."""
    from cursor_bridge import grok_agent_ops as ops

    nm = str(name or "").strip()
    if not nm:
        return {"ok": False, "code": "name_required", "reason": "name is required"}
    if ops._port(display) is None:
        return ops._unknown(display)
    listed = await ops.list_grok_agents_async(
        display, include_hidden=True, include_retired=False
    )
    if listed.get("ok") is not True:
        return {
            "ok": False,
            "code": str(listed.get("code") or "adopt_list_failed"),
            "display": ops._display_key(display),
            "detail": listed,
        }
    rows = [
        row
        for row in (listed.get("agents") or [])
        if isinstance(row, dict) and str(row.get("name") or "").strip() == nm
    ]
    if pre_create_ids is None:
        if rows:
            return {
                "ok": False,
                "code": "adopt_snapshot_missing",
                "needs_operator": True,
                "display": ops._display_key(display),
                "matches": len(rows),
            }
        return {
            "ok": False,
            "code": "adopt_not_found",
            "needs_operator": True,
            "display": ops._display_key(display),
        }
    snap = pre_create_ids
    candidates = [
        row
        for row in rows
        if str(row.get("id") or "").strip()
        and str(row.get("id") or "").strip() not in snap
    ]
    if len(candidates) == 1:
        return {
            "ok": True,
            "agent_id": str(candidates[0].get("id") or ""),
            "adopted": True,
            "display": ops._display_key(display),
        }
    if len(candidates) > 1:
        return {
            "ok": False,
            "code": "adopt_ambiguous",
            "needs_operator": True,
            "display": ops._display_key(display),
            "matches": len(candidates),
        }
    if rows:
        return {
            "ok": False,
            "code": "adopt_preexisting_only",
            "needs_operator": True,
            "display": ops._display_key(display),
            "matches": len(rows),
        }
    return {
        "ok": False,
        "code": "adopt_not_found",
        "needs_operator": True,
        "display": ops._display_key(display),
    }


__all__ = ["adopt_agent_by_name"]
