"""Paged agent-bus scan for cursor-auto CLOSEOUT dedup at boot replay."""

from __future__ import annotations

import os
from typing import Any

import httpx
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client
from universal_logging import get_logger

logger = get_logger(__name__)

_PAGE_SIZE = 50
# GET /turns has no ``after_turn`` query param (route ignores unknown params and
# returns the tip window). A paged loop that advances on ``after_turn`` therefore
# re-fetches the same tip forever and accumulates unbounded bodies into RAM
# (2026-08-03 hang class — ~43 GB RSS pre-bind). Bound the window tightly.
_MAX_TIP_WINDOW = 200


async def fetch_turns_from(
    thread_id: str,
    *,
    after_turn: int,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Return recent turns with ``turn_number >= after_turn``; ``None`` on transport failure.

    Uses a single bounded tip fetch (``last<=_MAX_TIP_WINDOW``) and filters
    client-side. Does not page — the agent-bus list route does not honor
    ``after_turn`` on GET /turns.
    """
    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    window = _MAX_TIP_WINDOW
    try:
        async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=20.0) as client:
            resp = await client.get(
                "/turns",
                params={
                    "thread": thread_id,
                    "last": window,
                },
                headers=headers,
            )
            if resp.status_code >= 400:
                return None, f"bus_http_{resp.status_code}"
            payload = resp.json() or {}
            batch = list(payload.get("turns") or [])
        collected = [
            turn
            for turn in batch
            if int(turn.get("turn_number") or 0) >= after_turn
        ]
        return collected, None
    except (httpx.HTTPError, ValueError, OSError) as exc:
        logger.warning("closeout bus scan failed thread=%s: %s", thread_id, exc)
        return None, str(exc)


def find_closeout_for_dispatch(
    turns: list[dict[str, Any]],
    *,
    dispatch_id: str,
    from_agent: str = "cursor-auto",
) -> dict[str, Any] | None:
    """Return the first matching CLOSEOUT turn, if any."""
    for turn in turns:
        if turn.get("from") != from_agent:
            continue
        body = str(turn.get("body") or "")
        if "TYPE: CLOSEOUT" not in body:
            continue
        if f"dispatch_id: {dispatch_id}" not in body and dispatch_id not in body:
            continue
        return turn
    return None
