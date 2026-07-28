"""Async polling loop for ULG story projector under git-integration-worker."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from universal_logging import get_logger

from .projector import run_projector_once

logger = get_logger(__name__)

_DEFAULT_INTERVAL_S = 12 * 60  # 12 minutes — within spec 10–15 min window


def poll_interval_s() -> float:
    raw = os.environ.get("ULG_STORY_PROJECTOR_INTERVAL_S", "").strip()
    if not raw:
        return _DEFAULT_INTERVAL_S
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_INTERVAL_S
    return max(600.0, min(900.0, value))


async def ulg_story_projector_loop(_app: Any) -> None:
    """Background catch-up reader — failure-isolated from dispatch and relay."""
    interval = poll_interval_s()
    logger.info("ulg-story projector loop started interval_s=%.0f", interval)
    try:
        while True:
            try:
                result = await asyncio.to_thread(run_projector_once)
                if result.get("processed"):
                    logger.info(
                        "ulg-story projector pass processed=%s last_seq=%s",
                        result.get("processed"),
                        result.get("last_seq"),
                    )
            except Exception:  # noqa: BLE001 — never take down GIW
                logger.exception("ulg-story projector pass failed")
            await asyncio.sleep(interval)
    finally:
        logger.info("ulg-story projector loop stopped")
