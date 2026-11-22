"""Leg-4 counting spawn: resolve ``preferred_spawn``; never pass mailbox/thread_scope."""

from __future__ import annotations

from typing import Any

from cursor_bridge.grok_agent_ops import spawn_grok_agent
from cursor_bridge.grok_preferred_spawn import preferred_spawn_for
from cursor_bridge.grok_window_bindings import cdp_port_for_display


def spawn_counting_grok_agent(
    name: str,
    *,
    display: str | None = None,
    description: str = "",
    hidden: bool = True,
    text: str | None = None,
    voice: str = "self",
    grant: str | None = None,
    authorization_source: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Spawn for lean-ladder counting paths (omit or explicit display)."""
    pref = preferred_spawn_for()
    pref_display = str(pref.get("display") or "") if pref.get("ok") else None
    if display is None:
        if pref.get("ok") is not True:
            return {**pref, "op": "counting_spawn"}
        effective = str(pref["display"])
    else:
        effective = str(display).strip().lstrip(":")
        if cdp_port_for_display(effective) is None:
            return {
                "ok": False,
                "code": "unknown_display",
                "reason": f"unknown grok display {display!r}",
                "display": effective,
                "op": "counting_spawn",
            }
    out = spawn_grok_agent(
        effective,
        name,
        description=description,
        hidden=hidden,
        text=text,
        voice=voice,
        grant=grant,
        authorization_source=authorization_source,
        idempotency_key=idempotency_key,
    )
    report = {
        "preferred_spawn": pref_display,
        "effective_display": effective,
    }
    return {**out, **report, "spawn_report": report, "op": "counting_spawn"}


__all__ = ["spawn_counting_grok_agent"]