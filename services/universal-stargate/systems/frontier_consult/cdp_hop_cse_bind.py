"""Bind the agent-bus CSE pointer when a hop successor's chat URL is seated.

Stargate's CDP generate worker calls this from the seated callback. Request-time
``maybe_bind_thread_cse`` has no URL yet. Only ``mission_kind=hop`` writes, and
``bound_by`` is the mint path so a later relay paste cannot replace the successor.
"""

from __future__ import annotations

from universal_logging import get_logger

logger = get_logger(__name__)


def associate_hop_seated_cse(
    *,
    mission_kind: str | None,
    thread_id: str,
    parent_thread: str | None,
    chat_url: str,
    registration_id: str | None,
) -> None:
    """Append the lane CSE association once a hop successor chat URL is known.

    Non-hop seating returns without a write. ``parent_thread`` is the lane
    pointer when set; otherwise ``thread_id``. Fail-soft: a missing thread or
    store error is logged and does not fail the generate poll. ``bound_by``
    is ``cdp.generate.seated``, not a relay agent name. Empty ``chat_url``
    does not write.
    """
    if (mission_kind or "").strip().lower() != "hop":
        return
    lane = (parent_thread or "").strip() or (thread_id or "").strip()
    if not lane or not (chat_url or "").strip():
        return
    from agent_bus_store.db.cse_associations import (
        HOP_SEATED_BOUND_BY,
        associate_cse,
    )

    try:
        associate_cse(
            thread_id=lane,
            cse_chat_url=chat_url,
            cse_registration_id=registration_id,
            bound_by=HOP_SEATED_BOUND_BY,
            evidence="cdp.generate.seated",
        )
    except Exception as exc:  # noqa: BLE001 — seated stamp must not fail the poll
        logger.warning(
            "hop seated cse bind failed lane=%s registration_id=%s err=%s",
            lane,
            registration_id,
            exc,
        )
