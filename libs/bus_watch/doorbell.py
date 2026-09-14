"""Render the static WAKE doorbell prompt for claude.ai scheduled liaison tasks.

The paste is the doorbell (F2): addresses + episodic frame only; payload stays
on the bus/graph. Amendment A1 digest delivery; memos 10479#118 (plant an
address, never a dump) and #120 (direct ``Use the <slug> skill`` lines).
"""

from __future__ import annotations

DOORBELL_CAP = 1024
DEFAULT_SKILLS = ("liaison", "reasoning-posture")

SUCCESSOR_WAKE_CAP = 2048

__all__ = [
    "DEFAULT_SKILLS",
    "DOORBELL_CAP",
    "SUCCESSOR_WAKE_CAP",
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


def render_doorbell(
    root: str,
    slug: str,
    *,
    ring: str | None = None,
    skills: tuple[str, ...] = DEFAULT_SKILLS,
    extra_addresses: tuple[str, ...] = (),
    fired_by: str | None = None,
    cap: int = DOORBELL_CAP,
) -> str:
    """Static doorbell text for ``root``; echoes go to ``ring`` (the root when None).

    Identical for equal arguments (F2 M5) and capped so extra ``md_read`` addresses
    cannot grow the paste into a dump; over ``cap`` raises ``ValueError``. The DIGEST
    fetch window is 10 turns, not 3: root housekeeping (admits, INFO, CP pointers)
    outran a 3-turn window by six turns on 2026-09-12 (agent-bus:10479#139).
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
            f'echo: agent_bus(send, thread={echo}, subject="ORIENTED {root}", body="ORIENTED / digest: <DIGEST subject> — attention <n>, checkpoint_due <bool> / objective: <root.last_subject> / chat: <url> / tools: <count>")',
            f"commission: cursor_request(new_slug=r15-wake-<slug>, parent_thread={root}, lane_role=sub_mission, …) — if attention mint; quiet echo; ¬thread={root}.",
        ]
    )
    message = "\n".join(lines) + "\n"
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
