"""Bind the agent-bus CSE pointer when a hop successor's chat URL is seated.

Stargate's CDP generate worker calls this from the seated callback. Request-time
``maybe_bind_thread_cse`` has no URL yet. Hop and houseless-first-seat writes use
distinct mint ``bound_by`` values so relay paste cannot replace the successor.
"""

from __future__ import annotations

from universal_logging import get_logger

logger = get_logger(__name__)

_STAMP_POSTED: set[tuple[str, str, str]] = set()


def _read_prompt_uri_text(prompt_uri: str | None) -> str | None:
    uri = (prompt_uri or "").strip()
    if not uri:
        return None
    try:
        from systems.frontier_consult.prompt_expand_prelude import read_prompt_uri

        return read_prompt_uri(uri)
    except Exception as exc:  # noqa: BLE001 — birth resolution must not fail seating
        logger.warning(
            "hop seated could not read prompt_uri=%s for successor_birth_id: %s",
            uri,
            exc,
        )
        return None


_HOP_REQUEST_DATA_MARKER = "## Hop request (data)"
_CONTINUITY_HANDOFF_MARKER = "TYPE: CONTINUITY_HANDOFF"
_BIRTH_ID_LINE_PREFIX = "successor_birth_id:"


def _prompt_segment_for_successor_birth_id(prompt_text: str) -> str:
    """Slice rendered prompt; predecessor handoff prose is not authoritative."""
    text = prompt_text or ""
    hop_idx = text.rfind(_HOP_REQUEST_DATA_MARKER)
    handoff_idx = text.rfind(_CONTINUITY_HANDOFF_MARKER)
    if hop_idx >= 0 and hop_idx >= handoff_idx:
        return text[hop_idx + len(_HOP_REQUEST_DATA_MARKER) :].lstrip("\n")
    if handoff_idx >= 0:
        return text[handoff_idx:]
    return text


def _parse_successor_birth_id_from_rendered_prompt(prompt_text: str) -> str | None:
    from hop_handoff.body import is_successor_birth_id

    segment = _prompt_segment_for_successor_birth_id(prompt_text)
    found: str | None = None
    for line in segment.splitlines():
        if not line.startswith(_BIRTH_ID_LINE_PREFIX):
            continue
        value = line.split(":", 1)[1].strip()
        if is_successor_birth_id(value):
            found = value
    return found


def _resolve_successor_birth_id_for_stamp(
    *,
    successor_birth_id: str | None,
    prompt_uri: str | None,
) -> str | None:
    birth = (successor_birth_id or "").strip()
    if birth:
        return birth
    prompt_text = _read_prompt_uri_text(prompt_uri)
    if not prompt_text:
        return None
    return _parse_successor_birth_id_from_rendered_prompt(prompt_text)


def _persist_client_platform(registration_id: str, client_platform: str | None) -> None:
    """Best-effort registry adjunct; not proof of lane claim."""
    platform = (client_platform or "").strip()
    if not platform:
        return
    try:
        from claude_bundles import cdp_registry_store as store

        with store.ports_lock():
            active = store.load_active()
            row = active.get(registration_id)
            if not isinstance(row, dict):
                return
            updated = dict(row)
            updated["client_platform"] = platform
            active[registration_id] = updated
            store.write_active(active)
    except Exception as exc:  # noqa: BLE001 — adjunct must not fail seating
        logger.warning(
            "houseless seated client_platform persist failed reg=%s err=%s",
            registration_id,
            exc,
        )


def _post_tab_bind_seat_registration_stamp(
    *,
    lane: str,
    chat_url: str,
    registration_id: str,
    execution_id: str,
    successor_birth_id: str | None,
    prompt_uri: str | None,
    parent_thread: str,
    forced_successor_birth_id: str | None = None,
) -> None:
    reg = registration_id.strip()
    exec_id = execution_id.strip()
    if not reg or not exec_id:
        return
    dedupe_key = (lane, exec_id, reg)
    if dedupe_key in _STAMP_POSTED:
        return
    birth = (forced_successor_birth_id or "").strip() or None
    if not birth:
        birth = _resolve_successor_birth_id_for_stamp(
            successor_birth_id=successor_birth_id,
            prompt_uri=prompt_uri,
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


def _post_houseless_tab_bind_seat_registration_stamp(
    *,
    mailbox_thread: str,
    chat_url: str,
    registration_id: str,
    execution_id: str,
    successor_birth_id: str,
    client_platform: str | None = None,
    attested_by: str | None = None,
) -> None:
    reg = registration_id.strip()
    exec_id = execution_id.strip()
    lane = (mailbox_thread or "").strip()
    if not reg or not exec_id or not lane:
        return
    dedupe_key = (lane, exec_id, reg)
    if dedupe_key in _STAMP_POSTED:
        return
    from agent_bus_store.db.turns import insert_turn
    from hop_handoff.body import build_houseless_seat_registration_stamp

    body = build_houseless_seat_registration_stamp(
        successor_birth_id=successor_birth_id,
        registration_id=reg,
        execution_id=exec_id,
        mailbox_thread=lane,
        chat_url=chat_url,
        client_platform=client_platform,
        attested_by=attested_by,
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
            "houseless seat registration stamp failed lane=%s reg=%s err=%s",
            lane,
            reg,
            exc,
        )


def _houseless_seated_lane(
    *,
    mission_kind: str | None,
    thread_id: str,
    parent_thread: str | None,
    mailbox_thread: str | None,
    caller_agent: str | None,
) -> str | None:
    kind = (mission_kind or "").strip().lower()
    if kind == "hop":
        return None
    if (parent_thread or "").strip():
        return None
    from agent_bus_store.houseless_mailbox_thread import resolve_mailbox_thread

    return resolve_mailbox_thread(
        mailbox_thread=mailbox_thread,
        caller_agent=caller_agent,
        thread_id=thread_id,
    )


def _associate_houseless_first_seated_cse(
    *,
    thread_id: str,
    chat_url: str,
    registration_id: str | None,
    execution_id: str,
    mailbox_thread: str | None = None,
    caller_agent: str | None = None,
    client_platform: str | None = None,
    attested_by: str | None = None,
) -> None:
    """Bind and stamp a houseless mailbox on its first seated generate."""
    from agent_bus_store.houseless_mailbox_thread import resolve_mailbox_thread

    lane = (mailbox_thread or "").strip() or (thread_id or "").strip()
    if not lane or not (chat_url or "").strip():
        return
    from agent_bus_store.db.cse_associations import (
        HOUSELESS_FIRST_SEATED_BOUND_BY,
        associate_cse,
    )
    from hop_handoff.body import execution_id_as_successor_birth_id

    try:
        associate_cse(
            thread_id=lane,
            cse_chat_url=chat_url,
            cse_registration_id=registration_id,
            bound_by=HOUSELESS_FIRST_SEATED_BOUND_BY,
            evidence="cdp.generate.seated",
        )
    except Exception as exc:  # noqa: BLE001 — seated bind must not fail the poll
        logger.warning(
            "houseless first seated cse bind failed lane=%s registration_id=%s err=%s",
            lane,
            registration_id,
            exc,
        )
        return
    reg = (registration_id or "").strip()
    birth = execution_id_as_successor_birth_id(execution_id)
    if reg and birth:
        _persist_client_platform(reg, client_platform)
        _post_houseless_tab_bind_seat_registration_stamp(
            mailbox_thread=lane,
            chat_url=chat_url,
            registration_id=reg,
            execution_id=execution_id,
            successor_birth_id=birth,
            client_platform=client_platform,
            attested_by=attested_by,
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
    prompt_uri: str | None = None,
    mailbox_thread: str | None = None,
    caller_agent: str | None = None,
    client_platform: str | None = None,
    attested_by: str | None = None,
) -> None:
    """Append the lane CSE association once a hop successor chat URL is known.

    Non-hop seating returns without a write unless the mailbox is a houseless
    first-seat lane (no generate ``parent_thread``, parentless bus thread).
    ``parent_thread`` is the lane pointer when set; otherwise ``thread_id``.
    Fail-soft: a missing thread or store error is logged and does not fail the
    generate poll. ``bound_by`` is ``cdp.generate.seated``, not a relay agent
    name. Empty ``chat_url`` does not write. After ``associate_cse``, posts
    ``TYPE: SEAT_REGISTRATION`` via :func:`build_seat_registration_stamp` (B1).
    """
    kind = (mission_kind or "").strip().lower()
    if kind != "hop":
        houseless_lane = _houseless_seated_lane(
            mission_kind=mission_kind,
            thread_id=thread_id,
            parent_thread=parent_thread,
            mailbox_thread=mailbox_thread,
            caller_agent=caller_agent,
        )
        if houseless_lane:
            _associate_houseless_first_seated_cse(
                thread_id=thread_id,
                chat_url=chat_url,
                registration_id=registration_id,
                execution_id=execution_id,
                mailbox_thread=houseless_lane,
                caller_agent=caller_agent,
                client_platform=client_platform,
                attested_by=attested_by,
            )
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
            prompt_uri=prompt_uri,
            parent_thread=lane,
        )
