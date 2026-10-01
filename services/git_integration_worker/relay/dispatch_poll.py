"""Poll a nested cursor-sdk dispatch until terminal and fetch its closeout body."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client
from universal_logging import get_logger

from services.git_integration_worker.cursor_bus import CursorBusClient
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.relay.nested_poll_row import (
    resolve_nested_poll_row,
)

logger = get_logger(__name__)

_TERMINAL = frozenset({"completed", "failed"})
_POLL_INTERVAL_S = 2.0
_DEFAULT_TIMEOUT_S = 3600.0
_HEARTBEAT_FRESH_S = 60.0
_DEFAULT_MAX_POLL_REENTRIES = 10

def _dispatch_timeout_s() -> float:
    raw = os.environ.get("CURSOR_AUTO_DISPATCH_TIMEOUT_S", "").strip()
    if not raw:
        return _DEFAULT_TIMEOUT_S
    return max(30.0, float(raw))


def _heartbeat_fresh_s() -> float:
    raw = os.environ.get("CURSOR_AUTO_DISPATCH_HEARTBEAT_FRESH_S", "").strip()
    if not raw:
        return _HEARTBEAT_FRESH_S
    return max(10.0, float(raw))


def _max_poll_reentries() -> int:
    raw = os.environ.get("CURSOR_AUTO_DISPATCH_POLL_REENTRIES", "").strip()
    if not raw:
        return _DEFAULT_MAX_POLL_REENTRIES
    return max(1, int(raw))


def dispatch_row_liveness_fresh(
    row: dict[str, Any] | None,
    *,
    fresh_s: float | None = None,
    now: datetime | None = None,
) -> bool:
    """Return True when the nested dispatch row shows recent liveness.

    ``last_heartbeat_at`` is preferred; when absent (dispatch not heartbeating
    yet) ``started_at`` is used so a newly admitted row is not treated as dead.
    """
    if row is None:
        return False
    threshold = fresh_s if fresh_s is not None else _heartbeat_fresh_s()
    clock = now or datetime.now(UTC)
    cutoff = clock.timestamp() - threshold
    ts = row.get("last_heartbeat_at") or row.get("started_at")
    if ts is None:
        return False
    try:
        parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.timestamp() >= cutoff
    except ValueError:
        return False


async def poll_dispatch_terminal(
    *,
    thread_id: str,
    dispatch_id: str,
    timeout_s: float | None = None,
    superseded: Callable[[], bool] | None = None,
    on_tick: Callable[[dict[str, Any] | None], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Poll ledger until nested dispatch reaches a terminal status.

    *superseded* is checked each tick so a job displaced by a newer same-thread
    request abandons the poll immediately instead of burning the remaining
    dispatch budget on an episode the operator already backtracked.
    """
    ledger = CursorDispatchLedger.instance()
    budget = timeout_s if timeout_s is not None else _dispatch_timeout_s()
    deadline = time.monotonic() + budget
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        if superseded is not None and superseded():
            return {
                "ok": False,
                "terminal": False,
                "superseded": True,
                "dispatch_id": dispatch_id,
            }
        row = await asyncio.to_thread(
            resolve_nested_poll_row,
            ledger,
            thread_id=thread_id,
            dispatch_id=dispatch_id,
        )
        if row is not None:
            last = row
            if on_tick is not None:
                await on_tick(row)
            if row.get("status") in _TERMINAL:
                return {
                    "ok": True,
                    "terminal": True,
                    "status": row["status"],
                    "row": row,
                }
        await asyncio.sleep(_POLL_INTERVAL_S)
    return {
        "ok": False,
        "terminal": False,
        "reason": "dispatch_poll_timeout",
        "last": last,
        "dispatch_id": dispatch_id,
    }


async def poll_dispatch_terminal_with_liveness(
    *,
    thread_id: str,
    dispatch_id: str,
    timeout_s: float | None = None,
    superseded: Callable[[], bool] | None = None,
    on_tick: Callable[[dict[str, Any] | None], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Poll until terminal, extending budget while nested dispatch liveness is fresh."""
    budget = timeout_s if timeout_s is not None else _dispatch_timeout_s()
    total_ceiling_s = budget * (_max_poll_reentries() + 1)
    poll_started = time.monotonic()
    reentries = 0
    polled: dict[str, Any] = {}
    while True:
        polled = await poll_dispatch_terminal(
            thread_id=thread_id,
            dispatch_id=dispatch_id,
            timeout_s=budget,
            superseded=superseded,
            on_tick=on_tick,
        )
        if polled.get("terminal") or polled.get("superseded"):
            return polled
        if not dispatch_row_liveness_fresh(polled.get("last")):
            return polled
        if reentries >= _max_poll_reentries():
            logger.warning(
                "nested poll re-entry count ceiling dispatch_id=%s "
                "reentries=%s",
                dispatch_id,
                reentries,
            )
            return polled
        if time.monotonic() - poll_started >= total_ceiling_s:
            logger.warning(
                "nested poll total wall-clock ceiling dispatch_id=%s "
                "elapsed_s=%.1f ceiling_s=%.1f",
                dispatch_id,
                time.monotonic() - poll_started,
                total_ceiling_s,
            )
            return polled
        reentries += 1
        last = polled.get("last") or {}
        logger.info(
            "nested poll budget exhausted with fresh heartbeat; "
            "re-entering dispatch_id=%s reentry=%s last_heartbeat_at=%s",
            dispatch_id,
            reentries,
            last.get("last_heartbeat_at"),
        )


async def fetch_sdk_closeout_body(
    *,
    thread_id: str,
    dispatch_id: str,
    bus: CursorBusClient | None = None,
) -> str | None:
    """Return latest cursor-sdk bus turn body mentioning ``dispatch_id``."""
    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=15.0) as client:
            resp = await client.get(
                "/turns",
                params={"thread": thread_id, "last": 8},
                headers=headers,
            )
        if resp.status_code >= 400:
            return None
        turns = (resp.json() or {}).get("turns") or []
    except (httpx.HTTPError, ValueError, OSError):
        return None
    needle = dispatch_id[:8]
    for turn in reversed(turns):
        if turn.get("from") != "cursor-sdk":
            continue
        subject = str(turn.get("subject") or "")
        body = str(turn.get("body") or "")
        if dispatch_id in subject or needle in subject or needle in body:
            return body or None
    return None

