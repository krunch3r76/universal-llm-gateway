"""First-episode admit BRIEFING for cursor-auto v0."""

from __future__ import annotations

import os
from typing import Any

import httpx
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client

_FROM_AUTO = "cursor-auto"
_ADMIT_SUBJECT_PREFIX = "status:admitted"
_TURN_FETCH_LIMIT = 200
_MAX_BRIEFING_LINES = 18


def is_first_episode_admit(turns: list[dict[str, Any]]) -> bool:
    """True when no prior cursor-auto admit turn exists on the thread."""
    for turn in turns:
        if turn.get("from") != _FROM_AUTO:
            continue
        subject = str(turn.get("subject") or "")
        if subject.startswith(_ADMIT_SUBJECT_PREFIX):
            return False
    return True


def build_briefing_block(*, live_deltas: str | None = None) -> str:
    """Build ``TYPE: BRIEFING`` block (≤~15 lines, teammate offer register)."""
    deltas = live_deltas if live_deltas else "live_deltas: (none this episode)"
    lines = [
        "TYPE: BRIEFING",
        "cursor-auto lane — first request on this thread this episode.",
        "",
        "I can take code-seat ops for you:",
        "- manage / charter_reload — service lifecycle, charter windows",
        "- observability — liveness, busy_status, lane health",
        "- lifecycle — nested dispatch, gate status, closeout relay",
        "",
        "Outside perspective: fleet (cursor) often encourages Fable via Cowork",
        "picker/multitask when architecture-suitability is live — you may also",
        "self-route Fable; cursor/claude-opus-5 is an escalation option (inform Kaywan).",
        "",
        deltas,
        "",
        "For what's optimal next, use contract=confer on a follow-up request.",
    ]
    block = "\n".join(lines)
    if len(lines) > _MAX_BRIEFING_LINES:
        raise ValueError(f"BRIEFING exceeds {_MAX_BRIEFING_LINES} lines")
    return block


async def fetch_thread_turns(thread_id: str) -> list[dict[str, Any]] | None:
    """Fetch recent thread turns; ``None`` on transport/parse failure (fail-closed)."""
    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=15.0) as client:
            resp = await client.get(
                "/turns",
                params={"thread": thread_id, "last": _TURN_FETCH_LIMIT},
                headers=headers,
            )
        if resp.status_code >= 400:
            return None
        payload = resp.json()
        return list((payload or {}).get("turns") or [])
    except (httpx.HTTPError, ValueError, OSError, TypeError):
        return None


async def maybe_briefing_for_admit(
    thread_id: str,
    *,
    fetch_turns=None,
) -> str | None:
    """Return BRIEFING block for first episode, or ``None`` to omit.

    ``fetch_turns`` defaults to ``fetch_thread_turns`` resolved at call time
    so tests can monkeypatch the module attribute.
    """
    fetch = fetch_turns if fetch_turns is not None else fetch_thread_turns
    turns = await fetch(thread_id)
    if turns is None:
        return None
    if not is_first_episode_admit(turns):
        return None
    return build_briefing_block()


def compose_admit_body(base_body: str, briefing: str | None) -> str:
    """Piggyback BRIEFING into admit reply when present."""
    if not briefing:
        return base_body
    return f"{base_body}\n\n{briefing}"
