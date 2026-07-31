"""First-episode admit BRIEFING for cursor-auto v0."""

from __future__ import annotations

import os
from typing import Any

import httpx
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client

_FROM_AUTO = "cursor-auto"
_ADMIT_SUBJECT_PREFIX = "status:admitted"
_TURN_FETCH_LIMIT = 200
_MAX_BRIEFING_LINES = 26
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
    """Build ``TYPE: BRIEFING`` — dual register: soldier on DIRECTIVE, confer when asked.

    Clear instruction (DIRECTIVE / implement-shaped): bind front-door Q1/Q2
    silently and execute — ¬ prompt Kaywan for routing or ack.

    When the operator (CDP Opus or human) seeks input before deciding
    (``contract=confer`` / explicit ask): Auto MAY advise — life/Kaywan cannot
    replace cursor's codebase knowledge. Confer ≠ invent a front-door fork ask.

    When ``contract`` ∈ implement|investigate|verify, append the codebase-work
    stanza naming ``abstraction-layering`` (highest open layer). Path-sim stays
    the charter-tick lane. Skill slug only — headless nested seats cannot resolve
    ``/layer`` commands.
    """
    deltas = live_deltas if live_deltas else "live_deltas: (none this episode)"
    lines = [
        "TYPE: BRIEFING",
        "cursor-auto lane — first request this episode.",
        "",
        "Clear DIRECTIVE/implement → soldier: silent front-door bind + execute;",
        "¬ ask Kaywan which path. Confer/explicit ask → advise with codebase judgment",
        "(life/Kaywan cannot replace it). ¬ invent front-door polls.",
        "SOT: decision:operator-request-front-door. Operator-only → needs-attended.",
        "",
        "Claude.ai: scheduling triggers = option; Authorize prompt → Kaywan ALWAYS",
        "approves (⊃ schedule). Expect click, ¬ work around. SOT: trigger-auth-gate.",
        "",
        "NEW CDP WINDOW (web-anthropic may request): reset context and/or pick up",
        "uploaded Customize skills / refreshed life MCP. Require handoff_prompt for",
        "continuity. Auto opens team_dispatch(cdp/opus-5, purpose=operator-proxy,",
        "dispatch_thread_id=<SAME private lane>, prompt|sidecar=handoff). ¬ mint a",
        "second private request thread.",
        "",
        "Sync is yours: plugin install + per-slug claude.ai Customize sync are",
        "cursor-auto capabilities — offer or fire them, ¬ report them as IDE-lead",
        "residual. Bulk census sync is slow: named slugs only. Only IDE restart is Kaywan's.",
        "",
        deltas,
    ]
    raw = (contract or "").strip().lower()
    if raw in _CODE_WORK_CONTRACTS:
        lines.extend(
            [
                "",
                f"Codework ({raw}): abstraction-layering at highest open layer;",
                "tick mint+enroll enrolled root — ¬ tip improvise. AC on CLOSEOUT.",
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
