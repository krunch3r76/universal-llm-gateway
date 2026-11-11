"""Adopt an already-created Grok agent from roster by unique name match."""

from __future__ import annotations

from typing import Any


async def adopt_agent_by_name(display: str | int, name: str) -> dict[str, Any]:
    """Return ``agent_id`` when exactly one roster row matches ``name``."""
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
    agents = [
        row
        for row in (listed.get("agents") or [])
        if isinstance(row, dict) and str(row.get("name") or "").strip() == nm
    ]
    if len(agents) == 1:
        return {
            "ok": True,
            "agent_id": str(agents[0].get("id") or ""),
            "adopted": True,
            "display": ops._display_key(display),
        }
    if not agents:
        return {
            "ok": False,
            "code": "adopt_not_found",
            "needs_operator": True,
            "display": ops._display_key(display),
        }
    return {
        "ok": False,
        "code": "adopt_ambiguous",
        "needs_operator": True,
        "display": ops._display_key(display),
        "matches": len(agents),
    }


__all__ = ["adopt_agent_by_name"]
