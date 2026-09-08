"""``park-for-restart`` sweep — park every live dispatch for one restart intent.

The manage drain supervisor (step 1b, ``park_live=true``) and the recycle
park-first rung call this through ``POST /api/v1/cursor/park-for-restart``.
Idempotent on ``(intent_id, drain_epoch)``: rows already parked for the intent
report under ``already_parked`` and are not re-signalled, so the supervisor
may re-drive it exactly like ``begin-drain``. ``live_after`` counts only
occupants a restart still has to wait on — self-clearing refusals
(``NOT_LIVE_HERE``, ``RUN_ALREADY_TERMINAL``) close on their own.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from universal_logging import get_logger

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)
from services.git_integration_worker.cursor_sdk_park_events import (
    emit_sdk_park_refused,
    emit_sdk_park_sweep,
)
from services.git_integration_worker.cursor_sdk_park_for_restart import (
    ParkSignalResult,
    signal_park,
)
from services.git_integration_worker.cursor_sdk_park_preflight import (
    HARD_REFUSALS,
    SELF_CLEARING_REFUSALS,
    ParkRefusal,
)
from services.git_integration_worker.cursor_sdk_supersede import is_dispatch_live

logger = get_logger(__name__)


@dataclass(slots=True)
class ParkSweepSummary:
    intent_id: str
    drain_epoch: int | None
    requested: list[str] = field(default_factory=list)
    refused: list[dict[str, str]] = field(default_factory=list)
    already_parked: list[str] = field(default_factory=list)
    live_after: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "drain_epoch": self.drain_epoch,
            "requested": list(self.requested),
            "refused": list(self.refused),
            "already_parked": list(self.already_parked),
            "live_after": self.live_after,
        }

    @property
    def hard_refusals(self) -> list[dict[str, str]]:
        hard = {h.value for h in HARD_REFUSALS}
        return [r for r in self.refused if r["refusal"] in hard]


def _sweep_candidates(intent_id: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Return ``(live_candidates, already_parked_ids)`` for one intent."""
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        rows = conn.execute(
            "SELECT dispatch_id, thread_id, status FROM cursor_sdk_dispatches "
            "WHERE COALESCE(read_only,0)=0 AND status IN ('admitted','running') "
            "ORDER BY rowid ASC"
        ).fetchall()
        parked = conn.execute(
            "SELECT dispatch_id FROM cursor_sdk_dispatches "
            "WHERE park_kind IS NOT NULL AND park_intent_id=? ORDER BY rowid ASC",
            (intent_id,),
        ).fetchall()
    return (
        [{k: r[k] for k in r.keys()} for r in rows],
        [str(r["dispatch_id"]) for r in parked],
    )


def park_for_restart_sweep(
    *,
    intent_id: str,
    drain_epoch: int | None,
    actor: str,
    reason: str,
) -> ParkSweepSummary:
    """Park every live write-capable dispatch for one restart intent (blocking)."""
    summary = ParkSweepSummary(intent_id=intent_id, drain_epoch=drain_epoch)
    candidates, summary.already_parked = _sweep_candidates(intent_id)
    live_ids = [
        c["dispatch_id"]
        for c in candidates
        if is_dispatch_live(dispatch_id=c["dispatch_id"])
    ]
    for row in candidates:
        if row["dispatch_id"] in live_ids:
            continue
        emit_sdk_park_refused(
            dispatch_id=str(row["dispatch_id"]),
            refusal=ParkRefusal.NOT_LIVE_HERE.value,
            intent_id=intent_id,
            actor=actor,
        )
        summary.refused.append(
            {
                "dispatch_id": str(row["dispatch_id"]),
                "refusal": ParkRefusal.NOT_LIVE_HERE.value,
            }
        )
    results: list[ParkSignalResult] = []
    if live_ids:
        with ThreadPoolExecutor(max_workers=min(4, len(live_ids))) as pool:
            results = list(
                pool.map(
                    lambda did: signal_park(
                        did,
                        intent_id=intent_id,
                        drain_epoch=drain_epoch,
                        actor=actor,
                        reason=reason,
                    ),
                    live_ids,
                )
            )
    for result in results:
        if result.requested:
            summary.requested.append(result.dispatch_id)
        elif result.already_parked:
            summary.already_parked.append(result.dispatch_id)
        else:
            assert result.refusal is not None
            summary.refused.append(
                {"dispatch_id": result.dispatch_id, "refusal": result.refusal.value}
            )
    self_clearing = {r.value for r in SELF_CLEARING_REFUSALS}
    summary.live_after = sum(
        1 for r in summary.refused if r["refusal"] not in self_clearing
    )
    emit_sdk_park_sweep(
        intent_id=intent_id,
        drain_epoch=drain_epoch,
        requested=list(summary.requested),
        refused=list(summary.refused),
        already_parked=list(summary.already_parked),
        live_after=summary.live_after,
    )
    logger.info(
        "cursor-sdk park sweep intent_id=%s requested=%s refused=%s already_parked=%s "
        "live_after=%s",
        intent_id,
        summary.requested,
        summary.refused,
        summary.already_parked,
        summary.live_after,
    )
    return summary


__all__ = ["ParkSweepSummary", "park_for_restart_sweep"]
