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
# Codebase-maintenance contracts — denser hello stanza; other contracts stay soft.
_CODE_WORK_CONTRACTS = frozenset({"implement", "investigate", "verify"})


def is_first_episode_admit(turns: list[dict[str, Any]]) -> bool:
    """True when no prior cursor-auto admit turn exists on the thread."""
    for turn in turns:
        if turn.get("from") != _FROM_AUTO:
            continue
        subject = str(turn.get("subject") or "")
        if subject.startswith(_ADMIT_SUBJECT_PREFIX):
            return False
    return True


def build_briefing_block(
    *,
    live_deltas: str | None = None,
    contract: str | None = None,
) -> str:
    """Build ``TYPE: BRIEFING`` — soft versatile hello; denser when code work.

    First-episode register is life/teammate, not a maintenance console.
    When ``contract`` ∈ implement|investigate|verify, append a short
    codebase-work stanza; wire tokens and AC evidence stay on CLOSEOUT.
    """
    deltas = live_deltas if live_deltas else "live_deltas: (none this episode)"
    lines = [
        "TYPE: BRIEFING",
        "cursor-auto lane — first request on this thread this episode.",
        "",
        "Versatile teammate: route a consult or question to another model,",
        "take operational checks, or do codebase work when that's the job.",
        "Not only maintenance — ask what you need.",
        "",
        "When an architecture question is live, Fable via Cowork is encouraged;",
        "you may also self-route. Premium cursor Opus is an escalation",
        "(inform Kaywan).",
        "",
        deltas,
        "",
        "Ask what's optimal next on a follow-up and I'll confer.",
    ]
    raw = (contract or "").strip().lower()
    if raw in _CODE_WORK_CONTRACTS:
        lines.extend(
            [
                "",
                f"This request is codebase work ({raw}): I'll nest a dispatch",
                "and return CLOSEOUT with AC evidence. Ops detail lives there,",
                "not in this hello.",
            ]
        )
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
    contract: str | None = None,
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
    return build_briefing_block(contract=contract)


def compose_admit_body(base_body: str, briefing: str | None) -> str:
    """Piggyback BRIEFING into admit reply when present."""
    if not briefing:
        return base_body
    return f"{base_body}\n\n{briefing}"
