"""Stargate retry sweep for delivering closeout-memo rows. Not the overdue watch."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from typing import Any

from closeout_memo.events import emit_closeout_memo
from closeout_memo.models import CloseoutMemoRequest
from closeout_memo.render import render_memos

from . import _ledger, _transport
from .coalesce import _lane_lock
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


async def _post_overflow(wake_lane: str, memo_ids: list[str], overflow: str) -> None:
    text = overflow.strip()
    if not text or not memo_ids:
        return
    await _transport.post_bus_turn(
        wake_lane=wake_lane,
        subject=f"closeout memo — overflow {memo_ids[0][:8]}",
        body=text,
    )


async def _recover_stale_admitted() -> None:
    """Render admitted rows the process dropped between admit and coalesce."""
    by_lane: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _ledger.stale_admitted():
        by_lane[str(row["wake_lane"])].append(row)
    for wake_lane in by_lane:
        async with _lane_lock(wake_lane):
            claimed = _ledger.claim_admitted(wake_lane, limit=5)
            if not claimed:
                continue
            memos = [
                CloseoutMemoRequest.model_validate(json.loads(row["payload_json"]))
                for row in claimed
            ]
            rendered = render_memos(memos)
            memo_ids = [str(row["memo_id"]) for row in claimed]
            _ledger.store_render(
                memo_ids,
                text=rendered.text,
                sha256=rendered.sha256,
                overflow=rendered.overflow_bus_text,
            )
            if rendered.bus_only or not rendered.text:
                await _fallback_group(
                    memo_ids,
                    wake_lane,
                    body=rendered.overflow_bus_text or rendered.text,
                    error="bus_only" if rendered.bus_only else "no_render",
                )
                continue
            outcome = await apply_decision(
                memo_ids=memo_ids,
                wake_lane=wake_lane,
                prompt_text=rendered.text,
                followup=_transport.post_followup,
                harvest=_transport.harvest_marker,
            )
            if outcome.get("delivered"):
                await _post_overflow(wake_lane, memo_ids, rendered.overflow_bus_text)
            elif outcome.get("needs_fallback"):
                await _fallback_group(
                    memo_ids,
                    wake_lane,
                    body="\n".join(
                        part
                        for part in (rendered.text, rendered.overflow_bus_text)
                        if part
                    ),
                    error=str(outcome.get("error") or "undelivered"),
                )


async def sweep_once() -> int:
    """Re-deliver due rows. Returns how many groups were visited."""
    await _recover_stale_admitted()
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
        overflow = str(group[0].get("overflow_bus_text") or "")
        text = str(group[0].get("rendered_text") or "")
        body = "\n".join(part for part in (text, overflow) if part.strip())
        if _age_exceeded(group[0]) or int(group[0].get("attempts") or 0) >= 6:
            await _fallback_group(
                memo_ids,
                wake_lane,
                body=body,
                error="sweep_ceiling",
            )
            continue
        if not text:
            await _fallback_group(memo_ids, wake_lane, body=body, error="no_render")
            continue
        async with _lane_lock(wake_lane):
            outcome = await apply_decision(
                memo_ids=memo_ids,
                wake_lane=wake_lane,
                prompt_text=text,
                followup=_transport.post_followup,
                harvest=_transport.harvest_marker,
            )
        if outcome.get("delivered"):
            await _post_overflow(wake_lane, memo_ids, overflow)
        elif outcome.get("needs_fallback"):
            await _fallback_group(
                memo_ids,
                wake_lane,
                body=body,
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
