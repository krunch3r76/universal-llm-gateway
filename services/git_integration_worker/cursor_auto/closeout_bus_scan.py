"""Paged agent-bus scan for cursor-auto CLOSEOUT dedup at boot replay."""

from __future__ import annotations

import os
from typing import Any

import httpx
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client
from universal_logging import get_logger

logger = get_logger(__name__)

_PAGE_SIZE = 50


async def fetch_turns_from(
    thread_id: str,
    *,
    after_turn: int,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Return turns with ``turn_number >= after_turn``; ``None`` list on transport failure."""
    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    collected: list[dict[str, Any]] = []
    cursor = max(0, after_turn - 1)
    try:
        async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=20.0) as client:
            while True:
                resp = await client.get(
                    "/turns",
                    params={
                        "thread": thread_id,
                        "after_turn": cursor,
                        "last": _PAGE_SIZE,
                    },
                    headers=headers,
                )
                if resp.status_code >= 400:
                    return None, f"bus_http_{resp.status_code}"
                payload = resp.json() or {}
                batch = list(payload.get("turns") or [])
                if not batch:
                    break
                for turn in batch:
                    turn_no = int(turn.get("turn_number") or 0)
                    if turn_no >= after_turn:
                        collected.append(turn)
                last_no = int(batch[-1].get("turn_number") or cursor)
                if len(batch) < _PAGE_SIZE:
                    break
                cursor = last_no
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
