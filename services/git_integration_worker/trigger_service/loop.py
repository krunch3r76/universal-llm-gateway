"""Background fire + reconcile loop for trigger service."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from typing import Any

from universal_logging import get_logger

from services.git_integration_worker.events import publish_lib_signal
from services.git_integration_worker.trigger_service.fire import (
    fire_once,
    reconcile_row,
)
from services.git_integration_worker.trigger_service.store import TriggerStore
from services.git_integration_worker.trigger_service.story_envelope import (
    emit_trigger_signal,
)

logger = get_logger(__name__)

_DEFAULT_INTERVAL_S = 30.0
_DEFAULT_RECLAIM_S = 300.0


def fire_interval_s() -> float:
    raw = os.environ.get("TRIGGER_FIRE_INTERVAL_S", "").strip()
    if not raw:
        return _DEFAULT_INTERVAL_S
    try:
        return max(5.0, float(raw))
    except ValueError:
        return _DEFAULT_INTERVAL_S


def reclaim_stale_s() -> float:
    raw = os.environ.get("TRIGGER_RECLAIM_STALE_S", "").strip()
    if not raw:
        return _DEFAULT_RECLAIM_S
    try:
        return max(60.0, float(raw))
    except ValueError:
        return _DEFAULT_RECLAIM_S


async def _pager_on_fire(row_dict: dict[str, Any]) -> None:
    try:
        from pager_notify.client import notify_pager

        so_what = row_dict.get("so_what") or "Trigger fired"
        subject = f"Trigger: {so_what}"[:120]
        body = (
            f"id={row_dict.get('id')} exec={row_dict.get('execution_id')} "
            f"arc={row_dict.get('arc') or 'n/a'}"
        )[:300]
        await notify_pager(subject, body, tag="trigger-fire")
    except Exception:  # noqa: BLE001 — pager must not abort loop
        logger.exception("trigger fire pager notify failed id=%s", row_dict.get("id"))


async def run_trigger_pass(store: TriggerStore) -> dict[str, int]:
    """One fire pass: reclaim stale, expire due, claim due rows, submit."""
    now = datetime.now(UTC)
    stats = {
        "claimed": 0,
        "fired": 0,
        "retried": 0,
        "failed": 0,
        "reclaimed": 0,
        "expired": 0,
    }
    reclaimed = await asyncio.to_thread(
        store.reclaim_stale_firing,
        now=now,
        stale_after_s=reclaim_stale_s(),
    )
    for row in reclaimed:
        stats["reclaimed"] += 1
        emit_trigger_signal(
            "giw.trigger.reclaimed",
            row,
            publish=publish_lib_signal,
            claimed_at=row.claimed_at,
        )
    # expire_due emits giw.trigger.expired post-commit (same seam as claim_due)
    expired_rows = await asyncio.to_thread(store.expire_due, now=now)
    for _row in expired_rows:
        stats["expired"] += 1
    while True:
        row = await asyncio.to_thread(store.claim_due, now=now)
        if row is None:
            break
        stats["claimed"] += 1
        updated = await asyncio.to_thread(fire_once, store, row)
        if updated.status == "fired":
            stats["fired"] += 1
            await _pager_on_fire(updated.to_dict())
        elif updated.status == "scheduled":
            stats["retried"] += 1
        elif updated.status == "failed":
            stats["failed"] += 1
    return stats


async def run_reconcile_pass(store: TriggerStore) -> int:
    """Poll fired rows to terminal — separate from submit path."""
    rows = await asyncio.to_thread(store.list_pending_reconcile)
    reconciled = 0
    for row in rows:
        result = await asyncio.to_thread(reconcile_row, store, row)
        if result is not None:
            reconciled += 1
    return reconciled


async def trigger_fire_loop(_app: Any) -> None:
    """GIW lifespan background loop — fire due triggers, reconcile in separate pass."""
    interval = fire_interval_s()
    store = TriggerStore()
    logger.info("trigger fire loop started interval_s=%.0f", interval)
    try:
        while True:
            try:
                fire_stats = await run_trigger_pass(store)
                reconciled = await run_reconcile_pass(store)
                if any(fire_stats.values()) or reconciled:
                    logger.info(
                        "trigger pass fire=%s reconcile=%d",
                        fire_stats,
                        reconciled,
                    )
            except Exception:  # noqa: BLE001 — never take down GIW
                logger.exception("trigger fire loop pass failed")
            await asyncio.sleep(interval)
    finally:
        logger.info("trigger fire loop stopped")
