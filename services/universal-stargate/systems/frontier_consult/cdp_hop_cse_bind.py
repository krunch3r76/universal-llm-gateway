"""Bind the agent-bus CSE pointer when a hop successor's chat URL is seated.

Stargate's CDP generate worker calls this from the seated callback. Request-time
``maybe_bind_thread_cse`` has no URL yet. Only ``mission_kind=hop`` writes, and
``bound_by`` is the mint path so a later relay paste cannot replace the successor.
"""

from __future__ import annotations

from universal_logging import get_logger

logger = get_logger(__name__)

_STAMP_POSTED: set[tuple[str, str, str]] = set()


def _resolve_successor_birth_id_from_lane(lane: str) -> str | None:
    from agent_bus_store.db.turns import get_turns
    from hop_handoff.body import parse_successor_birth_id

    rows = get_turns(thread=lane, last=100)
    for row in reversed(rows):
        body = str(row.get("body") or "")
        birth = parse_successor_birth_id(body)
        if birth:
            return birth
    return None


def _post_tab_bind_seat_registration_stamp(
    *,
    lane: str,
    chat_url: str,
    registration_id: str,
    execution_id: str,
    successor_birth_id: str | None,
    parent_thread: str,
) -> None:
    reg = registration_id.strip()
    exec_id = execution_id.strip()
    if not reg or not exec_id:
        return
    dedupe_key = (lane, exec_id, reg)
    if dedupe_key in _STAMP_POSTED:
        return
    birth = (successor_birth_id or "").strip() or _resolve_successor_birth_id_from_lane(
        lane
    )
    if not birth:
        logger.warning(
            "hop seated seat registration stamp skipped: no successor_birth_id lane=%s",
            lane,
        )
        return
    from agent_bus_store.db.turns import insert_turn
    from hop_handoff.body import build_seat_registration_stamp

    body = build_seat_registration_stamp(
        successor_birth_id=birth,
        registration_id=reg,
        execution_id=exec_id,
        parent_thread=parent_thread,
        chat_url=chat_url,
    )
    try:
        insert_turn(
            thread=lane,
            from_agent="cursor-auto",
            to_agent="cursor",
            subject=f"SEAT_REGISTRATION — {reg}",
            body=body,
        )
        _STAMP_POSTED.add(dedupe_key)
    except Exception as exc:  # noqa: BLE001 — stamp must not fail the seated poll
        logger.warning(
            "hop seated seat registration stamp failed lane=%s reg=%s err=%s",
            lane,
            reg,
            exc,
        )


def associate_hop_seated_cse(
    *,
    mission_kind: str | None,
    thread_id: str,
    parent_thread: str | None,
    chat_url: str,
    registration_id: str | None,
    execution_id: str = "",
    successor_birth_id: str | None = None,
) -> None:
    """Append the lane CSE association once a hop successor chat URL is known.

    Non-hop seating returns without a write. ``parent_thread`` is the lane
    pointer when set; otherwise ``thread_id``. Fail-soft: a missing thread or
    store error is logged and does not fail the generate poll. ``bound_by``
    is ``cdp.generate.seated``, not a relay agent name. Empty ``chat_url``
    does not write. After ``associate_cse``, posts ``TYPE: SEAT_REGISTRATION``
    on the hop lane via :func:`build_seat_registration_stamp` (B1 tab bind).
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
        return
    reg = (registration_id or "").strip()
    if reg:
        _post_tab_bind_seat_registration_stamp(
            lane=lane,
            chat_url=chat_url,
            registration_id=reg,
            execution_id=execution_id,
            successor_birth_id=successor_birth_id,
            parent_thread=lane,
        )
