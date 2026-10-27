"""Mechanical-land sweep — fold trigger for bus-bound inputs (spec §3.2)."""

from __future__ import annotations

from universal_logging import get_logger

from services.git_integration_worker.mechanical_land.reactor import (
    ReactorDeps,
    repost_blocked_if_needed,
    run_reactor_for_run_id,
)
from services.git_integration_worker.mechanical_land.store import MechanicalLandStore

logger = get_logger(__name__)

_SWEEP_STATUSES = (
    "awaiting_review",
    "awaiting_restart",
    "blocked_hold",
    "admit_deferred",
)


def list_run_ids_by_status(
    store: MechanicalLandStore,
    statuses: tuple[str, ...],
) -> list[str]:
    if not statuses:
        return []
    placeholders = ",".join("?" for _ in statuses)
    with store._connect() as conn:
        rows = conn.execute(
            f"SELECT run_id FROM mechanical_land_runs WHERE status IN ({placeholders})",
            statuses,
        ).fetchall()
    return [str(r["run_id"]) for r in rows]


def sweep_mechanical_land_runs(
    *,
    store: MechanicalLandStore | None = None,
    deps: ReactorDeps | None = None,
) -> int:
    """Run the reactor loop for sweep-eligible runs; return count touched."""
    use_store = store or MechanicalLandStore()
    run_ids = list_run_ids_by_status(use_store, _SWEEP_STATUSES)
    touched = 0
    for run_id in run_ids:
        try:
            run_reactor_for_run_id(run_id, deps=deps)
            repost_blocked_if_needed(use_store, run_id)
            touched += 1
        except Exception:  # noqa: BLE001
            logger.exception("mechanical_land sweep failed run_id=%s", run_id)
    return touched
