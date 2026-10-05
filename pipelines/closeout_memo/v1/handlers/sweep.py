"""Stargate retry sweep for delivering closeout-memo rows. Not the overdue watch."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

from closeout_memo.events import emit_closeout_memo

from . import _ledger, _transport
from .deliver import apply_decision

logger = logging.getLogger(__name__)

_SWEEP_INTERVAL_S = 30.0
_started = False


def _age_exceeded(row: dict[str, Any]) -> bool:
    import calendar
    import time

    created = str(row.get("created_at") or "")
    try:
        created_epoch = calendar.timegm(time.strptime(created, "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return False
    return time.time() - created_epoch > 15 * 60


async def sweep_once() -> int:
    """Re-deliver due rows. Returns how many groups were visited."""
    rows = _ledger.due_delivering()
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = str(row.get("rendered_sha256") or row["memo_id"])
        groups[key].append(row)
    visited = 0
    for group in groups.values():
        visited += 1
        memo_ids = [str(row["memo_id"]) for row in group]
        wake_lane = str(group[0]["wake_lane"])
        if _age_exceeded(group[0]) or int(group[0].get("attempts") or 0) >= 6:
            await _fallback_group(
                memo_ids,
                wake_lane,
                body=str(group[0].get("rendered_text") or ""),
                error="sweep_ceiling",
            )
            continue
        text = str(group[0].get("rendered_text") or "")
        if not text:
            await _fallback_group(memo_ids, wake_lane, body="", error="no_render")
            continue
        outcome = await apply_decision(
            memo_ids=memo_ids,
            wake_lane=wake_lane,
            prompt_text=text,
            followup=_transport.post_followup,
            harvest=_transport.harvest_marker,
        )
        if outcome.get("needs_fallback"):
            await _fallback_group(
                memo_ids,
                wake_lane,
                body=text,
                error=str(outcome.get("error") or "undelivered"),
            )
    return visited


async def _fallback_group(
    memo_ids: list[str], wake_lane: str, *, body: str, error: str
) -> None:
    kind = "sdk_closeout"
    status = "failed"
    if memo_ids:
        row = _ledger.load(memo_ids[0])
        if row is not None:
            kind = str(row.get("kind") or kind)
            status = str(row.get("status") or status)
    memo8 = memo_ids[0][:8] if memo_ids else "unknown"
    subject = f"closeout memo — undelivered {memo8} {kind} status:{status}"
    await _transport.post_bus_turn(wake_lane=wake_lane, subject=subject, body=body)
    if wake_lane and _ledger.claim_page(wake_lane):
        await _transport.page_lane(wake_lane=wake_lane, subject=subject, body=body[:400])
    _ledger.mark_undelivered(memo_ids, error=error)
    emit_closeout_memo(
        "undelivered", memo_ids=memo_ids, wake_lane=wake_lane, error=error
    )


async def retry_sweep_loop(stop: asyncio.Event | None = None) -> None:
    """Loop until ``stop`` is set. Interval is 30s; the ceiling is 15 minutes."""
    while stop is None or not stop.is_set():
        try:
            await sweep_once()
        except Exception:  # noqa: BLE001 — sweep must stay up
            logger.exception("closeout memo retry sweep failed")
        try:
            if stop is None:
                await asyncio.sleep(_SWEEP_INTERVAL_S)
            else:
                await asyncio.wait_for(stop.wait(), timeout=_SWEEP_INTERVAL_S)
        except TimeoutError:
            continue


def start_retry_sweep() -> asyncio.Task[None] | None:
    """Start the sweep once per process. No-op when no loop is running."""
    global _started
    if _started:
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    _started = True
    return loop.create_task(retry_sweep_loop(), name="closeout-memo-retry")
