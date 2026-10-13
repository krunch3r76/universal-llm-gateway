"""Between-window consumption of harvest-wanted propagation ledger rows."""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

from charter_runner_store.propagation_ledger import (
    DEFER_HARVEST_WANTED,
    close_row,
    fail_row,
    list_harvest_wanted_rows,
    reclaim_stale_consumption_claims,
    release_consumption_claim,
    set_defer_reason,
    try_claim_for_consumption,
)
from charter_runner_store.propagation_outcomes import append_propagation_outcome

from .propagation_execute import (
    dispatch_for_projection,
    giw_restart_precondition,
    proof_matches,
    row_may_fire_at_harvest,
)

if TYPE_CHECKING:
    from universal_event_bus import EventBus

    from scripts.model_manager.ui.controller.service_ctl.core import ServiceController

logger = logging.getLogger(__name__)


async def consume_harvest_wanted_at_tick(
    *,
    tick_index: int,
    service_controller: ServiceController,
    event_bus: EventBus | None = None,
) -> dict[str, Any]:
    """Fire harvest-wanted rows once per charter tick — proof closes via close_row."""
    from scripts.model_manager.ui.api_dispatch import sync_restart_charter_harvest

    reclaimed = reclaim_stale_consumption_claims()
    rows = list_harvest_wanted_rows()
    if not rows:
        return {
            "status": "ok",
            "tick_index": tick_index,
            "reclaimed_stale_claims": reclaimed,
            "attempted": 0,
            "closed": [],
            "failed": [],
            "deferred": [],
        }

    closed: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    service_results: dict[str, Any] = {}

    for row in rows:
        token = f"tick-{tick_index}-{uuid.uuid4().hex[:12]}"
        projection = {
            "row_id": row.row_id,
            "service": row.service,
            "code_ref": row.code_ref,
            "safe_window": row.safe_window,
            "age_in_harvests": row.age_in_harvests,
            "proof_class_requested": row.proof_class,
        }
        if not try_claim_for_consumption(row.row_id, token):
            deferred.append({**projection, "defer_reason": "claim_not_acquired"})
            continue

        dispatch_before = dispatch_for_projection(row)
        if dispatch_before.error is not None:
            fail_row(
                row.row_id,
                proof_payload={
                    "proof_class_requested": dispatch_before.proof_class_requested,
                    "proof_class_executed": dispatch_before.proof_class_executed,
                },
                reason=dispatch_before.error,
            )
            outcome = {
                **projection,
                "outcome": "failed",
                "reason": dispatch_before.error,
                "tick_index": tick_index,
                "consumption_token": token,
            }
            append_propagation_outcome(outcome)
            failed.append(outcome)
            continue

        before = dispatch_before.payload
        may_fire, window_reason = row_may_fire_at_harvest(row)
        i2_ok, i2_reason = giw_restart_precondition(row)
        if not may_fire or not i2_ok:
            decline_reason = i2_reason if not i2_ok else window_reason
            release_consumption_claim(row.row_id, token)
            deferred.append(
                {
                    **projection,
                    "outcome": "declined",
                    "decline_reason": decline_reason,
                    "defer_reason": DEFER_HARVEST_WANTED,
                }
            )
            continue

        try:
            outcome_manage = await sync_restart_charter_harvest(
                service_controller,
                row.service,
                event_bus=event_bus,
            )
            service_results[row.service] = outcome_manage
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "harvest_wanted sync_restart failed service=%s tick=%s",
                row.service,
                tick_index,
            )
            release_consumption_claim(row.row_id, token)
            defer = f"sync_restart_error:{type(exc).__name__}"
            set_defer_reason(row.row_id, defer)
            deferred.append({**projection, "defer_reason": defer})
            continue

        manage_status = str(outcome_manage.get("status") or "")
        if manage_status in ("error", "skipped"):
            fail_row(
                row.row_id,
                proof_payload={"manage": outcome_manage},
                reason=str(outcome_manage.get("reason") or manage_status),
            )
            outcome = {
                **projection,
                "outcome": "failed",
                "reason": outcome_manage.get("reason") or manage_status,
                "manage": outcome_manage,
                "tick_index": tick_index,
                "consumption_token": token,
            }
            append_propagation_outcome(outcome)
            failed.append(outcome)
            continue

        if manage_status == "deferred":
            release_consumption_claim(row.row_id, token)
            decline_reason = str(
                outcome_manage.get("reason")
                or outcome_manage.get("state")
                or "manage_deferred"
            )
            deferred.append(
                {
                    **projection,
                    "outcome": "declined",
                    "decline_reason": decline_reason,
                    "defer_reason": DEFER_HARVEST_WANTED,
                    "manage": outcome_manage,
                }
            )
            continue

        dispatch_after = dispatch_for_projection(row)
        if dispatch_after.error is not None:
            fail_row(
                row.row_id,
                proof_payload={
                    "proof_class_requested": dispatch_after.proof_class_requested,
                    "proof_class_executed": dispatch_after.proof_class_executed,
                },
                reason=dispatch_after.error,
            )
            outcome = {
                **projection,
                "outcome": "failed",
                "reason": dispatch_after.error,
                "tick_index": tick_index,
                "consumption_token": token,
            }
            append_propagation_outcome(outcome)
            failed.append(outcome)
            continue

        live_after = dispatch_after.payload
        requested_class = row.proof_class
        executed_class = dispatch_after.proof_class_executed
        proof_ok = executed_class == requested_class and proof_matches(
            row, live_after, before=before
        )
        close_payload = {
            **(live_after or {}),
            "proof_class_requested": requested_class,
            "proof_class_executed": executed_class,
            "manage": outcome_manage,
        }
        if proof_ok:
            close_row(row.row_id, proof_payload=close_payload)
            outcome = {
                **projection,
                "outcome": "proven",
                "proof": live_after,
                "proof_before": before,
                "tick_index": tick_index,
                "consumption_token": token,
            }
            append_propagation_outcome(outcome)
            closed.append(outcome)
        else:
            release_consumption_claim(row.row_id, token)
            defer = "proof_not_observed_after_restart"
            set_defer_reason(row.row_id, defer)
            outcome = {
                **projection,
                "outcome": "attempted_unproven",
                "defer_reason": defer,
                "proof": live_after,
                "proof_before": before,
                "tick_index": tick_index,
                "consumption_token": token,
            }
            append_propagation_outcome(outcome)
            deferred.append(outcome)

    return {
        "status": "ok",
        "tick_index": tick_index,
        "reclaimed_stale_claims": reclaimed,
        "attempted": len(rows),
        "closed": closed,
        "failed": failed,
        "deferred": deferred,
        "services": service_results,
    }


__all__ = ["consume_harvest_wanted_at_tick"]
