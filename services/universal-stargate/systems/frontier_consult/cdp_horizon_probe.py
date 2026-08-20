"""Horizon probe classification and seated-authorship alive-oracle.

Reconcile calls this module at the generate horizon. Satellite poll answers
attach, not CSE death; ``unverifiable`` must not collapse to ``ok=False``.
The second source is a turn authored by the seated CDP seat on ``leg.thread_id``,
not mere thread presence (cursor-auto admits/heartbeats/WAKEs do not count).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from typing import Any, Literal

from claude_bundles.cdp_model_endpoint import CDP_REPLY_FROM, terminal_failure
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client
from universal_logging import get_logger

logger = get_logger(__name__)

HorizonObservation = Literal["alive", "confirmed_dead", "unverifiable"]
SEATED_CDP_FROM_AGENT = CDP_REPLY_FROM
SUBSTRATE_FAILED_SUBJECT_MARKERS = ("cdp FAILED", "CDP generate FAILED")
AUTH_FETCH_LAST = 40

TurnsFetch = Callable[[str], Sequence[dict[str, Any]]]


def classify_horizon_probe(snapshot: dict[str, Any] | None) -> HorizonObservation:
    """Map a satellite poll snapshot onto alive, confirmed_dead, or unverifiable.

    Alive extends; confirmed_dead may abandon; unverifiable retains.
    """
    if snapshot is None:
        return "unverifiable"
    if snapshot.get("error") and "status" not in snapshot:
        return "unverifiable"
    status = str(snapshot.get("status") or "")
    if status in {"running", "pending"}:
        return "alive"
    if status in {"failed", "aborted"} or terminal_failure(snapshot):
        return "confirmed_dead"
    return "unverifiable"


def is_substrate_terminal_subject(subject: str) -> bool:
    """True when the turn is an on-behalf FAILED delivery, not CSE speech."""
    return any(marker in subject for marker in SUBSTRATE_FAILED_SUBJECT_MARKERS)


def seated_authorship_hit(
    turns: Sequence[dict[str, Any]],
    *,
    seated_from_agent: str = SEATED_CDP_FROM_AGENT,
    successor_birth_id: str | None = None,
) -> bool:
    """True when a turn is authored by the seated CDP seat, not lane presence.

    ``from_agent`` must match the seated identity (``web-anthropic``). Substrate
    ``cdp FAILED`` deliveries use that same ``from_agent`` and are excluded.
    When ``successor_birth_id`` is supplied, it must appear in subject or body.
    """
    seated = seated_from_agent.strip()
    if not seated:
        return False
    birth = (successor_birth_id or "").strip() or None
    for turn in turns:
        from_agent = str(turn.get("from_agent") or turn.get("from") or "").strip()
        if from_agent != seated:
            continue
        subject = str(turn.get("subject") or "")
        if is_substrate_terminal_subject(subject):
            continue
        if birth is not None:
            blob = f"{subject} {turn.get('body') or ''}"
            if birth not in blob:
                continue
        return True
    return False


def _agent_bus_headers() -> dict[str, str]:
    token = os.getenv("AGENT_BUS_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


async def fetch_recent_thread_turns(
    thread_id: str,
    *,
    last: int = AUTH_FETCH_LAST,
) -> list[dict[str, Any]]:
    """GET recent turns for the authorship oracle; transport errors return empty."""
    if not thread_id.strip():
        return []
    try:
        async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=10.0) as client:
            resp = await client.get(
                "/turns",
                params={
                    "thread": thread_id,
                    "last": last,
                    "compact": True,
                    "mark_read": False,
                },
                headers=_agent_bus_headers(),
            )
    except Exception as exc:  # noqa: BLE001 — oracle miss must not become death
        logger.warning(
            "cdp horizon authorship fetch transport error: thread=%s err=%s",
            thread_id,
            exc,
        )
        return []
    if resp.status_code >= 300:
        logger.warning(
            "cdp horizon authorship fetch failed: thread=%s status=%s body=%s",
            thread_id,
            resp.status_code,
            resp.text[:200],
        )
        return []
    payload = resp.json()
    turns = payload.get("turns") if isinstance(payload, dict) else payload
    if not isinstance(turns, list):
        return []
    return [row for row in turns if isinstance(row, dict)]


async def seated_authorship_on_thread(
    thread_id: str,
    *,
    seated_from_agent: str = SEATED_CDP_FROM_AGENT,
    successor_birth_id: str | None = None,
    turns: Sequence[dict[str, Any]] | None = None,
    fetch_fn: TurnsFetch | None = None,
) -> bool:
    """Resolve seated authorship on ``thread_id``.

    Fetch or transport errors are a miss, never death.
    """
    if turns is None:
        if fetch_fn is not None:
            fetched = fetch_fn(thread_id)
            turns = list(fetched)
        else:
            turns = await fetch_recent_thread_turns(thread_id)
    return seated_authorship_hit(
        turns,
        seated_from_agent=seated_from_agent,
        successor_birth_id=successor_birth_id,
    )
