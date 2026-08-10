"""First-episode admit BRIEFING for cursor-auto v0."""

from __future__ import annotations

import os
from typing import Any

import httpx
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client

_FROM_AUTO = "cursor-auto"
_ADMIT_SUBJECT_PREFIX = "status:admitted"
_TURN_FETCH_LIMIT = 200
_MAX_BRIEFING_LINES = 12
# Codebase-maintenance contracts — denser hello stanza; other contracts stay soft.
_CODE_WORK_CONTRACTS = frozenset({"implement", "investigate", "verify", "seed"})


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
    """Build ``TYPE: BRIEFING`` — per-episode admit content only.

    Standing seat posture (front-door register, CDP window, sync authority)
    lives in the ``cursor_request`` tool descriptor — read before authoring.
    Deploy/live law (``decision:checkout-disk-is-executable``) is also echoed
    here so Auto sees it on every first-episode admit without opening the
    descriptor.

    When ``contract`` ∈ implement|investigate|verify, append the codebase-work
    stanza naming ``abstraction-layering`` (highest open layer). When
    ``contract=seed``, append the seed-path stanza naming
    ``work-item-seed-path``. Path-sim stays the charter-tick lane.
    """
    deltas = live_deltas if live_deltas else "live_deltas: (none this episode)"
    lines = [
        "TYPE: BRIEFING",
        "cursor-auto lane — first request this episode.",
        "",
        deltas,
        # decision:checkout-disk-is-executable — standing deploy law (operator 2026-08-01)
        "Deploy: sync_restart loads checkout disk (committed∨not)."
        " landed≠live=¬restarted, ¬uncommitted.",
    ]
    raw = (contract or "").strip().lower()
    if raw in _CODE_WORK_CONTRACTS and raw != "seed":
        lines.extend(
            [
                "",
                f"Codework ({raw}): abstraction-layering at highest open layer;",
                "tick mint+enroll enrolled root — ¬ tip improvise. AC on CLOSEOUT.",
                "Closeout: path-explicit commit clears lane authorship;",
                "checkpoint_claim: committed|nothing_authored|deferred: required in §2.",
            ]
        )
    elif raw == "seed":
        lines.extend(
            [
                "",
                "Seed: work-item-seed-path S1→S6; Mode B ⇒ same-turn consult admit",
                "or named halt. CLOSEOUT: slug + consult URI + entry gate.",
            ]
        )
    elif raw == "confer":
        lines.extend(
            [
                "",
                "Confer: codebase-grounded recommendation; ¬ front-door routing poll.",
            ]
        )
    block = "\n".join(lines)
    if len(lines) > _MAX_BRIEFING_LINES:
        raise ValueError(f"BRIEFING exceeds {_MAX_BRIEFING_LINES} lines")
    return block


async def fetch_thread_status(thread_id: str) -> str | None:
    """Fetch agent-bus thread status; ``None`` on transport/parse failure (fail-open).

    Performs GET ``/threads/{thread_id}`` with the same bearer transport as
    ``fetch_thread_turns``. Normalizes status from top-level or nested
    ``thread.status`` (strip + lower). Callers treat ``None`` as unknown status.
    """
    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=15.0) as client:
            resp = await client.get(f"/threads/{thread_id}", headers=headers)
        if resp.status_code >= 400:
            return None
        detail = resp.json() or {}
        raw = detail.get("status")
        if raw is None and isinstance(detail.get("thread"), dict):
            raw = (detail.get("thread") or {}).get("status")
        if raw is None:
            return None
        return str(raw).strip().lower()
    except (httpx.HTTPError, ValueError, OSError, TypeError):
        return None


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
