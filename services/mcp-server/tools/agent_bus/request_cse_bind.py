"""Stamp a Cowork session address onto agent-bus thread metadata at request time.

Called after the request turn is written so a dead Auto handler cannot leave
the thread silent about which CSE authored the commission. A hop successor's
URL arrives later, on ``cdp.generate.seated``; this path must not replace that
mint row with a different session address from a relay or paste.
"""

from __future__ import annotations

from typing import Any

from mcp_events import record

from ._shared import relay
from .park_hint import is_chat_delivery_capable


def _retired_url_blocked_by_hop_successor(thread_id: str, cse_chat_url: str) -> bool:
    """True when the lane's current row is the hop mint and this URL differs.

    A GET failure does not block. The store still refuses that rewrite inside
    the append transaction if the mint row is already current. When agent-bus
    is down, the following POST fails soft in the caller.
    """
    from agent_bus_store.db.cse_associations import (
        HOP_SEATED_BOUND_BY,
        normalize_cse_bind_url,
    )

    try:
        current = relay("agent-bus", "GET", f"/threads/{thread_id}/cse-current")
    except Exception:  # noqa: BLE001 — unread current still attempts the POST
        return False
    if not isinstance(current, dict) or "error" in current:
        return False
    if current.get("bound_by") != HOP_SEATED_BOUND_BY:
        return False
    current_url = normalize_cse_bind_url(str(current.get("cse_chat_url") or ""))
    incoming = normalize_cse_bind_url(cse_chat_url)
    if not current_url or not incoming:
        return False
    return current_url != incoming


def maybe_bind_thread_cse(
    *,
    thread_id: str,
    from_agent: str,
    cse_chat_url: str | None,
    cse_registration_id: str | None,
    continuity_hop: bool = False,
) -> dict[str, Any] | None:
    """Append a CSE association after the request turn is written.

    Not gated on Auto enqueue or liveness. Registration-only is not a bind —
    the host route no-ops without a Cowork URL. A continuity hop from the IDE
    still binds: the census reads this row, and ``cursor`` is not a mailbox.
    Skips the associate call when the current row is the hop successor and
    this URL is a different session, so a paste into a retired chat does not
    move the bus pointer. Relays to agent-bus so the container does not open
    messages.db. Fail-soft so a bind miss cannot take down the request.
    """
    if not thread_id:
        return None
    if not continuity_hop and not is_chat_delivery_capable(from_agent):
        return None
    if not (cse_chat_url or "").strip():
        return None
    if _retired_url_blocked_by_hop_successor(thread_id, cse_chat_url):
        return None
    try:
        result = relay(
            "agent-bus",
            "POST",
            f"/threads/{thread_id}/cse-associate",
            body={
                "cse_chat_url": cse_chat_url,
                "cse_registration_id": cse_registration_id,
                "bound_by": from_agent,
                "evidence": "agent_bus.request",
            },
        )
    except Exception as exc:
        record(
            "mcp.agentbus.request.cse_bind_failed",
            thread=thread_id,
            error=str(exc),
        )
        return None
    if not isinstance(result, dict) or "error" in result:
        record(
            "mcp.agentbus.request.cse_bind_failed",
            thread=thread_id,
            error=str(result.get("error") if isinstance(result, dict) else result),
        )
        return None
    return result
