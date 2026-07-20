"""Standing CDP registry hygiene loop — ULG-owned (cdp-ask satellite).

Replaces remote systemd --user timers. Matches agent-bus watchdog /
execution_store reaper: asyncio sleep → work in to_thread → never crash.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL_S = 1200.0  # 20 min — same cadence as the retired timer


def hygiene_interval_s() -> float:
    raw = os.environ.get("CDP_REGISTRY_HYGIENE_INTERVAL_S", "").strip()
    if not raw:
        return _DEFAULT_INTERVAL_S
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_INTERVAL_S
    return value if value > 0 else _DEFAULT_INTERVAL_S


def run_hygiene_once() -> dict[str, Any]:
    """Sync extended reclaim — safe to call from asyncio.to_thread."""
    from claude_bundles import cdp_registry

    result = cdp_registry.hygiene_reclaim_extended()
    return {
        "reclaimed_ports": list(result.reclaimed_ports),
        "removed_profiles": list(result.removed_profiles),
    }


class RegistryHygieneLoop:
    """Background extended hygiene while the cdp-ask satellite is up."""

    def __init__(self, *, interval_s: float | None = None) -> None:
        self._interval_s = (
            hygiene_interval_s() if interval_s is None else float(interval_s)
        )
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(
                self._loop(), name="cdp-registry-hygiene"
            )

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                summary = await asyncio.to_thread(run_hygiene_once)
                if summary["reclaimed_ports"] or summary["removed_profiles"]:
                    logger.info(
                        "cdp registry hygiene reclaimed_ports=%s removed=%d",
                        summary["reclaimed_ports"],
                        len(summary["removed_profiles"]),
                    )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — standing loop must not die
                logger.exception("cdp registry hygiene sweep failed")
            await asyncio.sleep(self._interval_s)
