"""Render the static WAKE doorbell prompt for claude.ai scheduled liaison tasks.

The paste is the doorbell (F2): addresses + episodic frame only; payload stays
on the bus/graph. Amendment A1 digest delivery; memos 10479#118 (plant an
address, never a dump) and #120 (direct ``Use the <slug> skill`` lines).
"""

from __future__ import annotations

DOORBELL_CAP = 1024
DEFAULT_SKILLS = ("liaison", "reasoning-posture")

__all__ = ["DEFAULT_SKILLS", "DOORBELL_CAP", "render_doorbell"]


def _render_address(uri: str) -> str:
    if "#" in uri:
        path, section = uri.split("#", 1)
        return f"fs(op=md_read, path={path}, section={section})"
    return f"fs(op=md_read, path={uri})"


def render_doorbell(
    root: str,
    slug: str,
    *,
    ring: str | None = None,
    skills: tuple[str, ...] = DEFAULT_SKILLS,
    extra_addresses: tuple[str, ...] = (),
    cap: int = DOORBELL_CAP,
) -> str:
    """Static doorbell text for ``root``; echoes go to ``ring`` (the root when None).

    Identical for equal arguments (F2 M5) and capped so extra ``md_read`` addresses
    cannot grow the paste into a dump; over ``cap`` raises ``ValueError``.
    """
    echo = ring if ring else root
    address_parts = [
        f"agent_bus_read(fetch, thread={root}, last=3, compact=true)",
        f"agent-bus:{echo} (echo)",
    ]
    address_parts.extend(_render_address(addr) for addr in extra_addresses)
    addresses = "; ".join(address_parts)

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
            f"frame: fired by scheduled task liaison-wake-{root}; seat web-anthropic; prior wake = last ORIENTED turn on agent-bus:{echo}.",
            f'echo: agent_bus(send, thread={echo}, subject="ORIENTED {root}", body="ORIENTED / digest: <DIGEST subject> — attention <n>, checkpoint_due <bool> / objective: <root.last_subject> / chat: <url> / tools: <count>")',
            f"commission: cursor_request(thread={root}, …) — only when attention names work; a quiet digest ⇒ echo and stop.",
        ]
    )
    message = "\n".join(lines) + "\n"
    encoded = message.encode("utf-8")
    if len(encoded) > cap:
        raise ValueError(f"doorbell exceeds {cap} bytes ({len(encoded)})")
    return message
