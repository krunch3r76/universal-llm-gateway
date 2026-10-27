"""Boot-time mechanical-land reconcile (spec §3.2)."""

from __future__ import annotations

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


async def reconcile_mechanical_land_on_boot() -> int:
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
