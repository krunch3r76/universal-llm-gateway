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
    """Sync extended reclaim — safe to call from asyncio.to_thread.

    Draining live hosts to dormant runs first so the reclaim pass sees the freed
    ports and the aged-out dormant rows in the same sweep.
    """
    from claude_bundles import cdp_registry
    from claude_bundles.cdp_registry.dormant_drain import drain_live_hosts_to_dormant
    from claude_bundles.cse_session_obligations import sweep_wake_owed_ttl

    from cdp_ask.followup import lane_in_flight

    drain = drain_live_hosts_to_dormant(is_busy=lane_in_flight)
    dormant_reclaimed = cdp_registry.reclaim_dormant_rows()
    result = cdp_registry.hygiene_reclaim_extended()
    wake_alarms = sweep_wake_owed_ttl(
        notify_pager=_sync_notify_pager,
    )
    return {
        "reclaimed_ports": list(result.reclaimed_ports),
        "removed_profiles": list(result.removed_profiles),
        "wake_alarms": wake_alarms,
        "drained": drain.as_dict(),
        "dormant_reclaimed": dormant_reclaimed,
    }


def _sync_notify_pager(subject: str, body: str) -> bool:
    """Best-effort pager for TTL alarm — returns True when notify reports sent."""
    try:
        import asyncio

        from pager_notify.client import notify_pager

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(notify_pager(subject, body, tag="wake-ttl"))
        finally:
            loop.close()
        return bool(result)
    except Exception:
        logger.exception("wake TTL pager notify failed")
        return False


def run_orphan_scan_once() -> dict[str, Any]:
    """Sync observation-only orphan scan — emits event; never mutates registry."""
    from claude_bundles import cdp_orphans

    scan = cdp_orphans.find_orphans()
    return cdp_orphans.orphan_scan_as_dict(scan)


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
                drained = summary.get("drained", {}).get("counts", {})
                if drained.get("dormant") or drained.get("released"):
                    logger.info(
                        "cdp hosts drained dormant=%d released=%d protected=%d "
                        "dormant_reclaimed=%d",
                        drained["dormant"],
                        drained["released"],
                        drained["protected"],
                        len(summary.get("dormant_reclaimed", ())),
                    )
                scan_summary = await asyncio.to_thread(run_orphan_scan_once)
                if scan_summary["matched_count"] or scan_summary.get("closable_count"):
                    logger.info(
                        "cdp registry orphan_scan matched_count=%s closable_count=%s "
                        "protected_count=%s ports_examined=%s",
                        scan_summary["matched_count"],
                        scan_summary.get("closable_count", 0),
                        scan_summary.get("protected_count", 0),
                        scan_summary["ports_examined"],
                    )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — standing loop must not die
                logger.exception("cdp registry hygiene sweep failed")
            await asyncio.sleep(self._interval_s)
