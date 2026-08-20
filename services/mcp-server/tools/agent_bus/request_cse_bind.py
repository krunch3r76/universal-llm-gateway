"""Stamp a Cowork session address onto agent-bus thread metadata at request time.

Called after the request turn is written so a dead Auto handler cannot leave
the thread silent about which CSE authored the commission.
"""

from __future__ import annotations

from typing import Any

from mcp_events import record

from .park_hint import is_chat_delivery_capable


def maybe_bind_thread_cse(
    *,
    thread_id: str,
    from_agent: str,
    cse_chat_url: str | None,
    cse_registration_id: str | None,
) -> dict[str, Any] | None:
    """Append a CSE association after the request turn is written.

    Not gated on Auto enqueue or liveness. Registration-only is not a bind —
    ``associate_cse`` no-ops without a Cowork URL. Fail-soft so a bind miss
    cannot take down the request.
    """
    if not thread_id or not is_chat_delivery_capable(from_agent):
        return None
    if not (cse_chat_url or "").strip():
        return None
    try:
        from agent_bus_store.db.cse_associations import associate_cse

        return associate_cse(
            thread_id=thread_id,
            cse_chat_url=cse_chat_url,
            cse_registration_id=cse_registration_id,
            bound_by=from_agent,
            evidence="agent_bus.request",
        )
    except Exception as exc:
        record(
            "mcp.agentbus.request.cse_bind_failed",
            thread=thread_id,
            error=str(exc),
        )
        return None
