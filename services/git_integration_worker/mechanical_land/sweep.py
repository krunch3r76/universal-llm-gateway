"""Mechanical-land sweep — fold trigger for bus-bound inputs (spec §3.2)."""

from __future__ import annotations

import json

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


def list_blocked_repost_run_ids(store: MechanicalLandStore) -> list[str]:
    """``status=blocked`` runs whose latest ``blocked`` event still owes one bus repost."""
    with store._connect() as conn:
        rows = conn.execute(
            """
            SELECT j.run_id, j.payload_json
            FROM mechanical_land_journal j
            INNER JOIN mechanical_land_runs r ON r.run_id = j.run_id
            WHERE r.status = 'blocked'
              AND j.event = 'blocked'
              AND j.seq = (
                SELECT MAX(j2.seq) FROM mechanical_land_journal j2
                WHERE j2.run_id = j.run_id AND j2.event = 'blocked'
              )
            """
        ).fetchall()
    out: list[str] = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            continue
        if payload.get("posted_turn") is not None:
            continue
        attempts = int(payload.get("repost_attempts") or 0)
        if attempts > 0 or payload.get("_repost_attempted"):
            continue
        out.append(str(row["run_id"]))
    return out


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
    run_ids = list(
        dict.fromkeys(
            list_run_ids_by_status(use_store, _SWEEP_STATUSES)
            + list_blocked_repost_run_ids(use_store)
        )
    )
    touched = 0
    for run_id in run_ids:
        try:
            run_reactor_for_run_id(run_id, deps=deps)
            repost_blocked_if_needed(use_store, run_id)
            touched += 1
        except Exception:  # noqa: BLE001
            logger.exception("mechanical_land sweep failed run_id=%s", run_id)
    return touched
