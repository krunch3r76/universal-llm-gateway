"""Boot-time mechanical-land reconcile (spec §3.2)."""

from __future__ import annotations

import asyncio

from universal_logging import get_logger

from services.git_integration_worker.mechanical_land.reactor import (
    run_reactor_for_run_id,
)
from services.git_integration_worker.mechanical_land.store import MechanicalLandStore

logger = get_logger(__name__)


def list_open_run_ids(store: MechanicalLandStore) -> list[str]:
    with store._connect() as conn:
        rows = conn.execute(
            "SELECT run_id FROM mechanical_land_runs WHERE status NOT IN ('closed', 'blocked')"
        ).fetchall()
    return [str(r["run_id"]) for r in rows]


def reconcile_mechanical_land_on_boot_sync() -> int:
    """Fold every open run once; idempotent re-admit is handled in the reactor."""
    store = MechanicalLandStore()
    run_ids = list_open_run_ids(store)
    touched = 0
    for run_id in run_ids:
        try:
            run_reactor_for_run_id(run_id)
            touched += 1
        except Exception:  # noqa: BLE001
            logger.exception("mechanical_land boot reconcile failed run_id=%s", run_id)
    if touched:
        logger.info("mechanical_land boot reconcile runs=%d", touched)
    return touched


async def reconcile_mechanical_land_on_boot() -> int:
    """Run boot reconcile off the event loop (sqlite + optional bus I/O)."""
    return await asyncio.to_thread(reconcile_mechanical_land_on_boot_sync)
