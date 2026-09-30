"""Lane-keyed stand-down ACK probe for hop-cadence evaluate.

``evaluate_watch`` must not fire an age-hop through an open
``TYPE: SEAT_STAND_DOWN_ACK`` on the watched thread. The watch ledger and
Auto queue are not the truth source — callers inject this probe; it reads
the complete thread-scoped bus-turn history via async GET (no page cap —
a capped tip window can let an old open ACK scroll out of view) and
classifies TYPE tokens with ``cdp_ask.cse_session_ack.marker_type``. A later
``SUCCESSOR_ATTESTATION`` on the same thread consumes the pause. Transport
failure fails open (False) so cadence does not wedge shut.

Production cadence awaits ``fetch_thread_turns`` (``make_async_client``) then
injects the turns into ``lane_standdown_ack_open`` — never sync httpx on the
uvicorn loop, never ``asyncio.to_thread`` to hide sync HTTP.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import httpx
from cdp_ask.cse_session_ack import MarkerType, marker_type
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client, make_sync_client
from universal_logging import get_logger

logger = get_logger(__name__)

STANDDOWN_ACK_OPEN_REASON = "standdown_ack_open"

FetchTurnsFn = Callable[[str], list[dict[str, Any]] | None]


def _turn_number(turn: dict[str, Any]) -> int:
    try:
        return int(turn.get("turn_number") or 0)
    except (TypeError, ValueError):
        return 0


def _parse_turns_payload(
    thread_id: str, payload: Any
) -> list[dict[str, Any]] | None:
    """Shared JSON→turns validation for sync and async fetchers."""
    if not isinstance(payload, dict):
        logger.warning(
            "hop_cadence standdown fetch malformed thread=%s payload=non-dict",
            thread_id,
        )
        return None
    turns = payload.get("turns")
    if not isinstance(turns, list):
        logger.warning(
            "hop_cadence standdown fetch malformed thread=%s turns=non-list",
            thread_id,
        )
        return None
    if any(not isinstance(turn, dict) for turn in turns):
        logger.warning(
            "hop_cadence standdown fetch malformed thread=%s turn=non-mapping",
            thread_id,
        )
        return None
    return turns


async def fetch_thread_turns(thread_id: str) -> list[dict[str, Any]] | None:
    """Async GET /turns?thread=<id> (last omitted — full history).

    Calls ``transport_utils.make_async_client`` (same async agent-bus client
    family as ``episode_briefing.fetch_thread_turns``) so hop-cadence can
    await on the uvicorn loop. Sync httpx on that loop wedges ``/health``;
    ``asyncio.to_thread`` around sync httpx is the wrong shape for this house.
    None on transport/HTTP/parse failure.
    """
    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=15.0) as client:
            resp = await client.get(
                "/turns",
                params={"thread": thread_id},
                headers=headers,
            )
        if resp.status_code >= 400:
            logger.warning(
                "hop_cadence standdown fetch failed thread=%s status=%s",
                thread_id,
                resp.status_code,
            )
            return None
        return _parse_turns_payload(thread_id, resp.json())
    except (httpx.HTTPError, ValueError, OSError, TypeError) as exc:
        logger.warning(
            "hop_cadence standdown fetch failed thread=%s err=%s",
            thread_id,
            exc,
        )
        return None


def _fetch_thread_turns_sync(thread_id: str) -> list[dict[str, Any]] | None:
    """Sync GET /turns?thread=<id> (last omitted — full history).

    Retained for sync callers outside the cadence evaluate path
    (``hop_cadence_waiting``). Cadence standdown must await
    ``fetch_thread_turns`` instead.
    """
    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        with make_sync_client(DEFAULT_AGENT_BUS_URL, timeout=15.0) as client:
            resp = client.get(
                "/turns",
                params={"thread": thread_id},
                headers=headers,
            )
        if resp.status_code >= 400:
            logger.warning(
                "hop_cadence standdown fetch failed thread=%s status=%s",
                thread_id,
                resp.status_code,
            )
            return None
        return _parse_turns_payload(thread_id, resp.json())
    except (httpx.HTTPError, ValueError, OSError, TypeError) as exc:
        logger.warning(
            "hop_cadence standdown fetch failed thread=%s err=%s",
            thread_id,
            exc,
        )
        return None


def lane_standdown_ack_open(
    thread_id: str,
    *,
    fetch_turns_fn: FetchTurnsFn | None = None,
) -> bool:
    """Return True when the thread's latest typed marker is an open stand-down ACK.

    Truth source is the complete agent-bus turn list for *thread_id*
    (no page cap; include_superseded=false server-side, plus a local
    defense-in-depth skip on any turn the test seam hands in with
    status=="superseded"). Watch-ledger fields are not consulted.
    Absent/failed fetch → False (fail open). Empty thread_id → False.

    Production cadence must inject turns from ``await fetch_thread_turns``;
    the sync default exists only for hermetic tests and non-cadence sync
    callers.
    """
    tid = (thread_id or "").strip()
    if not tid:
        return False
    fetch = fetch_turns_fn if fetch_turns_fn is not None else _fetch_thread_turns_sync
    turns = fetch(tid)
    if turns is None or not isinstance(turns, list):
        return False
    if any(not isinstance(turn, dict) for turn in turns):
        return False
    ordered = sorted(turns, key=_turn_number)
    last: MarkerType | None = None
    for turn in ordered:
        if turn.get("status") == "superseded":
            continue
        kind = marker_type(str(turn.get("body") or ""))
        if kind is not None:
            last = kind
    return last == "stand_down_ack"
