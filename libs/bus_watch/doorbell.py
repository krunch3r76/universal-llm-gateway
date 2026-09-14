"""Render the static WAKE doorbell prompt for claude.ai scheduled liaison tasks.

The paste is the doorbell (F2): addresses + episodic frame only; payload stays
on the bus/graph. Amendment A1 digest delivery; memos 10479#118 (plant an
address, never a dump) and #120 (direct ``Use the <slug> skill`` lines).

``DOORBELL_CAP`` is 1400 bytes: basis floor is ~1132 B once the status quintuple
is carried; the ``commission:`` line costs exactly 203 B (measured); 1400 leaves
268 B of ladder headroom. The binder's rule is "the basis is never shed to fit a
cap; the cap moves."
"""

from __future__ import annotations

DOORBELL_CAP = 1400
DEFAULT_SKILLS = ("liaison", "reasoning-posture")

SUCCESSOR_WAKE_CAP = 2048

# Echo-body placeholders in ritual order; the paste stays byte-identical for
# equal arguments (F2 M5), so shedding removes fields, never reorders them.
# ``digest:`` carries the tip the wake is scored on and ``chat:`` is the CSE
# handle the seat reports back — only the other two are sheddable.
_DIGEST_PLACEHOLDER = "digest: <DIGEST subject> — attention <n>, checkpoint_due <bool>"
_ECHO_FIELDS = (
    _DIGEST_PLACEHOLDER,
    "objective: <root.last_subject>",
    "chat: <url>",
    "tools: <count>",
)
_SHEDDABLE_ECHO_FIELDS = ("tools: <count>", "objective: <root.last_subject>")
_COMMISSION_HINT = "if attention mint; quiet echo; "
_STALE_CONTRACT = "re-fetch DIGEST; on epoch mismatch echo STALE and stop"

__all__ = [
    "DEFAULT_SKILLS",
    "DOORBELL_CAP",
    "SUCCESSOR_WAKE_CAP",
    "basis_floor_bytes",
    "render_address",
    "render_doorbell",
    "render_successor_wake",
]


def render_address(uri: str) -> str:
    """Tool-call-shaped ``md_read`` address; ``#`` splits path from section."""
    if "#" in uri:
        path, section = uri.split("#", 1)
        return f"fs(op=md_read, path={path}, section={section})"
    return f"fs(op=md_read, path={uri})"


_render_address = render_address


def _digest_echo_quintuple(
    *,
    attention_row_ids: tuple[str, ...],
    as_of: str,
    digest_source: str,
    scope_root: str,
    scope_lanes: tuple[str, ...],
    fingerprint: str,
) -> str:
    ids = ",".join(attention_row_ids) if attention_row_ids else "(none)"
    lanes = ",".join(scope_lanes) if scope_lanes else ""
    scope = f"{scope_root}+{lanes}" if lanes else scope_root
    return (
        f"digest: value={ids} as_of={as_of} source={digest_source} "
        f"scope={scope} epoch={fingerprint} — {_STALE_CONTRACT}"
    )


def _quintuple_requested(
    *,
    attention_row_ids: tuple[str, ...] | None,
    as_of: str | None,
    digest_source: str | None,
    scope_lanes: tuple[str, ...] | None,
    fingerprint: str | None,
) -> bool:
    return all(
        v is not None
        for v in (attention_row_ids, as_of, digest_source, scope_lanes, fingerprint)
    )


def basis_floor_bytes(
    root: str,
    slug: str,
    *,
    attention_row_ids: tuple[str, ...] = (),
    as_of: str = "2026-09-14T07:00:00Z",
    digest_source: str = "a" * 16,
    scope_lanes: tuple[str, ...] = (),
    fingerprint: str = "b" * 16,
) -> int:
    """Byte length of a render with the full status quintuple and no sheddables."""
    text = render_doorbell(
        root,
        slug,
        ring=root,
        include_commission=False,
        attention_row_ids=attention_row_ids,
        as_of=as_of,
        digest_source=digest_source,
        scope_lanes=scope_lanes,
        fingerprint=fingerprint,
    )
    return len(text.encode("utf-8"))


def render_doorbell(
    root: str,
    slug: str,
    *,
    ring: str | None = None,
    skills: tuple[str, ...] = DEFAULT_SKILLS,
    extra_addresses: tuple[str, ...] = (),
    fired_by: str | None = None,
    cap: int = DOORBELL_CAP,
    include_commission: bool = True,
    attention_row_ids: tuple[str, ...] | None = None,
    as_of: str | None = None,
    digest_source: str | None = None,
    scope_lanes: tuple[str, ...] | None = None,
    fingerprint: str | None = None,
) -> str:
    """Static doorbell text for ``root``; echoes go to ``ring`` (the root when None).

    Identical for equal arguments (F2 M5) and capped so extra ``md_read`` addresses
    cannot grow the paste into a dump. A planted address outranks a placeholder: over
    ``cap`` the renderer sheds the optional echo fields and then the whole
    ``commission:`` line before raising ``ValueError``, so seating a live wake with
    one extra address is a render, not a hand-paste. The commission guard is never
    shed while the line remains — either the line is complete or absent (10479#881).
    Addresses, ``Use the <slug> skill`` lines, and the frame are never shed — a
    doorbell without its address is not a doorbell (10479#118). The
    DIGEST fetch window is 10 turns, not 3: root housekeeping (admits, INFO, CP
    pointers) outran a 3-turn window by six turns on 2026-09-12 (agent-bus:10479#139).
    """
    echo = ring if ring else root
    address_parts = [
        f"agent_bus_read(fetch, thread={root}, last=10, compact=true)",
        f"agent-bus:{echo} (echo)",
    ]
    address_parts.extend(render_address(addr) for addr in extra_addresses)
    addresses = "; ".join(address_parts)
    # Default keeps the scheduled-task frame (R15). CDP/live seating passes fired_by
    # so the episodic frame stays true (10158 M2) instead of a false ritual label.
    fire = fired_by if fired_by is not None else f"scheduled task liaison-wake-{root}"
    quintuple = _quintuple_requested(
        attention_row_ids=attention_row_ids,
        as_of=as_of,
        digest_source=digest_source,
        scope_lanes=scope_lanes,
        fingerprint=fingerprint,
    )
    if quintuple:
        digest_line = _digest_echo_quintuple(
            attention_row_ids=attention_row_ids or (),
            as_of=str(as_of),
            digest_source=str(digest_source),
            scope_root=root,
            scope_lanes=scope_lanes or (),
            fingerprint=str(fingerprint),
        )
        echo_fields = [digest_line, "chat: <url>"]
    else:
        echo_fields = list(_ECHO_FIELDS)
    show_commission = include_commission

    def compose() -> str:
        body = "ORIENTED / " + " / ".join(echo_fields)
        lines = [
            f"WAKE — liaison, house agent-bus:{root} {slug}",
            "duty: read the latest DIGEST; act only on its attention; page only on designed stops.",
            "disclosure: orientation ritual; one echo before the first move.",
            f'objective: latest DIGEST {root} turn (subject starts "DIGEST {root}").',
            f"addresses: {addresses}",
        ]
        lines.extend(f"Use the {skill} skill." for skill in skills)
        lines.extend(
            [
                f"frame: fired by {fire}; seat web-anthropic; prior wake = last ORIENTED turn on agent-bus:{echo}.",
                f'echo: agent_bus(send, thread={echo}, subject="ORIENTED {root}", body="{body}")',
            ]
        )
        if show_commission:
            lines.append(
                f"commission: cursor_request(new_slug=r15-wake-<slug>, parent_thread={root}, lane_role=sub_mission, …) — {_COMMISSION_HINT}¬thread={root}."
            )
        return "\n".join(lines) + "\n"

    def shed() -> bool:
        """Drop the least load-bearing fragment; False when only the ritual is left."""
        nonlocal show_commission
        if quintuple:
            return False
        for field in _SHEDDABLE_ECHO_FIELDS:
            if field in echo_fields:
                echo_fields.remove(field)
                return True
        if show_commission:
            show_commission = False
            return True
        return False

    message = compose()
    while len(message.encode("utf-8")) > cap and shed():
        message = compose()
    encoded = message.encode("utf-8")
    if len(encoded) > cap:
        raise ValueError(f"doorbell exceeds {cap} bytes ({len(encoded)})")
    return message


def render_successor_wake(
    root_id: str,
    *,
    gear: str,
    row: str,
    tip_cp_ordinal: int | None = None,
    ring: str | None = None,
    extra_addresses: tuple[str, ...] = (),
    cap: int = SUCCESSOR_WAKE_CAP,
) -> str:
    """Doorbell-shaped successor paste. Line 1 stays ``resume {root}`` (GIW fence).

    Same ritual as ``render_doorbell`` (duty / disclosure / objective / addresses /
    Use-line / frame / echo). No recall language — echo is a graph-read, not
    hypnotic memory (suggestion_orientation: fpsyg.2025.1433762).
    """
    echo = ring if ring else root_id
    tip_val = tip_cp_ordinal if tip_cp_ordinal is not None else ""
    extras = "".join(f"; {render_address(addr)}" for addr in extra_addresses)
    resume_args = f'{{"op":"resume","thread":"{root_id}"}}'
    message = (
        f"resume {root_id}\n\n"
        f"WAKE — liaison headless successor, house agent-bus:{root_id} — contract: none.\n"
        "duty: run the tick; checkpoint; hop only if hop_qualifies. "
        "Hop only when autonomous follow-up remains; HOLD_MERGE / empty NOW / quiet tick → STAY.\n"
        "disclosure: orientation ritual; one echo before the first move.\n"
        f"objective: tip CHECKPOINT on agent-bus:{root_id} "
        f"(tip_cp_ordinal={tip_val}); row={row}; gear: {gear}.\n"
        f"addresses: dispatch(tool=\"continuity\", arguments='{resume_args}'); "
        f"agent_bus_read(thread_get, thread={root_id}); agent-bus:{echo} (echo){extras}\n"
        "Use the liaison skill. LOAD the liaison skill body; do not skim.\n"
        "LOAD AND EXECUTE runbook:bus-consult-watcher (legs 1-3).\n"
        f"frame: spawned by liaison-ticker gear {gear}; seat cursor-sdk; "
        f"predecessor = prior lease holder on agent-bus:{root_id}. "
        "§ Peer-house: keep both; cdp/opus-5 → 2nd pool → cursor/claude-opus-5; "
        "¬ cursor/claude-fable-5-1; ¬ hop away unreconciled.\n"
        f'echo: agent_bus(send, thread={echo}, subject="ORIENTED {root_id}", '
        'body="ORIENTED / tip: <CHECKPOINT subject> cp_ordinal=<n> / row: <row> / seat: cursor-sdk") '
        "before the first mutating move.\n"
    )
    encoded = message.encode("utf-8")
    if len(encoded) > cap:
        raise ValueError(f"successor message exceeds {cap} bytes ({len(encoded)})")
    return message
