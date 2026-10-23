"""Re-point grok-bot-gc1 wake subscription when the gc1 roster agent id changes.

Called from ``GrokbotEgressWatchLoop._context_hop`` after each roster probe.
Writes thread-scoped ``agent_endpoint_subscriptions`` for agent ``grok-bot-gc1``
and thread ``15851`` only when exactly one roster row is named Stage Manager (gc1).
"""

from __future__ import annotations

from typing import Any

from scripts.model_manager.ui.controller.grokbot_egress_watch_logic import (
    GC1_ROSTER_NAME,
)

_GC1_AGENT = "grok-bot-gc1"
_GC1_THREAD_SCOPE = "15851"


def choose_gc1_agent_id(rows: list[dict[str, Any]]) -> str | None:
    """Return the stripped agent_id when exactly one row matches the gc1 roster name."""
    matches: list[str] = []
    for row in rows:
        if row.get("name") != GC1_ROSTER_NAME:
            continue
        raw = row.get("agent_id")
        if not isinstance(raw, str):
            continue
        agent_id = raw.strip()
        if not agent_id:
            continue
        matches.append(agent_id)
    if len(matches) != 1:
        return None
    return matches[0]


def run_gc1_repoint(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Subscribe grok-bot-gc1 to the chosen gc1 agent_id when it differs from the journal."""
    chosen = choose_gc1_agent_id(rows)
    if chosen is None:
        return {"wrote": False, "reason": "not_unique"}

    from agent_bus_store.db.endpoint_subscriptions import (
        current_subscription,
        subscribe_endpoint,
    )
    from cursor_bridge.grok_window_bindings import binding_for

    current = current_subscription(_GC1_AGENT, _GC1_THREAD_SCOPE)
    current_id_raw = (current or {}).get("agent_id")
    current_id = (
        current_id_raw.strip()
        if isinstance(current_id_raw, str)
        else None
    )
    if current_id == chosen:
        return {"wrote": False, "reason": "unchanged"}

    display: str | None = None
    if current is not None:
        row_display = current.get("display")
        if isinstance(row_display, str) and row_display.strip():
            display = row_display.strip()
    if display is None:
        binding = binding_for(_GC1_AGENT)
        if binding is not None:
            bound_display = binding.get("display")
            if isinstance(bound_display, str) and bound_display.strip():
                display = bound_display.strip()
    if display is None:
        return {"wrote": False, "reason": "display_unknown"}

    try:
        subscribe_endpoint(
            agent=_GC1_AGENT,
            thread_scope=_GC1_THREAD_SCOPE,
            surface="grok",
            display=display,
            title=GC1_ROSTER_NAME,
            agent_id=chosen,
            bound_by="grok-seat-repoint",
            evidence=f"roster {GC1_ROSTER_NAME}",
        )
    except Exception as exc:
        return {
            "wrote": False,
            "reason": "subscribe_failed",
            "error": type(exc).__name__,
        }
    return {"wrote": True}


__all__ = ["choose_gc1_agent_id", "run_gc1_repoint"]
