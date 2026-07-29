"""Terminal path for ``contract: propagate`` — mint rows and coordinate restart."""

from __future__ import annotations

import asyncio
from typing import Any

from charter_runner_store.propagation_ledger import (
    close_row,
    set_defer_reason,
    upsert_open_rows,
)
from implement_admission.propagation_row import PropagationRow

from services.git_integration_worker.cursor_auto.handler_terminal import (
    post_terminal_status,
)
from services.git_integration_worker.cursor_auto.manage_sock import sync_restart_service
from services.git_integration_worker.cursor_auto.propagate_admission import (
    PROPAGATE_CONTRACT,
    admit_propagate_body,
)
from services.git_integration_worker.cursor_auto.propagation_probe import (
    giw_i2_clear,
    probe_process_live,
    proof_observed,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob
from services.git_integration_worker.cursor_bus import CursorBusClient


async def run_propagation_in_seat(
    job: AutoJob,
    *,
    client: CursorBusClient,
    queue: Any,
    model: dict[str, Any],
    effort: dict[str, Any],
    gate_plan: dict[str, Any],
) -> dict[str, Any]:
    """Mint propagation ledger rows and fire drain-gated sync_restart when safe."""
    admission = admit_propagate_body(job.body)
    if not admission.approved:
        error = admission.error or {"reason": "propagate_admission_missing"}
        summary = str(error.get("summary", "propagate admission failed"))
        return await post_terminal_status(
            job,
            client=client,
            queue=queue,
            summary=summary,
            disposition="blocked",
            contract=PROPAGATE_CONTRACT,
            terminal_status="status:blocked",
            payload={"summary": summary, **error},
            failed=True,
        )

    stamped = [
        row.model_copy(
            update={
                "mint_thread": str(job.thread_id),
                "mint_turn": job.turn_number,
                "reason": row.reason or "operator restart request via cursor-auto",
            }
        )
        for row in admission.rows
    ]
    row_ids = upsert_open_rows(list(stamped))
    executions: list[dict[str, Any]] = []
    for row, row_id in zip(stamped, row_ids, strict=True):
        executions.append(await _execute_row(row, row_id=row_id))

    disposition = _disposition_for(executions)
    summary = _summary_for(disposition, executions)
    payload: dict[str, Any] = {
        "summary": summary,
        "disposition": disposition,
        "propagation": [row.model_dump() for row in stamped],
        "row_ids": row_ids,
        "executions": executions,
        "flags": list(admission.flags),
        "requested_model": model["requested"],
        "requested_effort": effort["requested"],
        "gate_plan": gate_plan,
        "request_turn": job.turn_number,
    }
    return await post_terminal_status(
        job,
        client=client,
        queue=queue,
        summary=summary,
        disposition=disposition,
        contract=PROPAGATE_CONTRACT,
        payload=payload,
    )


async def _execute_row(row: PropagationRow, *, row_id: str) -> dict[str, Any]:
    if row.safe_window == "harvest":
        set_defer_reason(row_id, "harvest_window_only")
        return {
            "service": row.service,
            "row_id": row_id,
            "status": "scheduled",
            "reason": "harvest_window_only",
        }
    if row.service == "git_integration_worker":
        clear, i2_reason = giw_i2_clear()
        if not clear:
            set_defer_reason(row_id, i2_reason)
            return {
                "service": row.service,
                "row_id": row_id,
                "status": "scheduled",
                "reason": i2_reason,
            }
    manage_result = await asyncio.to_thread(
        sync_restart_service,
        row.service,
        reason="operator propagate via cursor-auto",
    )
    status = str(manage_result.get("status") or "unknown")
    if status == "deferred":
        set_defer_reason(row_id, "manage_deferred_drain")
        return {
            "service": row.service,
            "row_id": row_id,
            "status": "deferred",
            "manage": manage_result,
        }
    if status == "error":
        set_defer_reason(row_id, str(manage_result.get("reason") or "manage_error"))
        return {
            "service": row.service,
            "row_id": row_id,
            "status": "failed",
            "manage": manage_result,
        }
    proof = await asyncio.to_thread(probe_process_live, row.service)
    if proof_observed(row, proof):
        close_row(row_id, proof_payload=proof or {})
        return {
            "service": row.service,
            "row_id": row_id,
            "status": "executed",
            "manage": manage_result,
            "proof": proof,
        }
    set_defer_reason(row_id, "proof_pending")
    return {
        "service": row.service,
        "row_id": row_id,
        "status": "submitted",
        "manage": manage_result,
        "proof": proof,
    }


def _disposition_for(executions: list[dict[str, Any]]) -> str:
    statuses = {str(item.get("status") or "") for item in executions}
    if statuses <= {"executed"}:
        return "executed"
    if "executed" in statuses or "deferred" in statuses or "submitted" in statuses:
        return "propagated"
    if statuses <= {"scheduled"}:
        return "scheduled"
    return "propagated"


def _summary_for(disposition: str, executions: list[dict[str, Any]]) -> str:
    services = ", ".join(str(item.get("service") or "?") for item in executions)
    if disposition == "executed":
        return f"Auto executed propagation restart for {services}; proof-of-live observed."
    if disposition == "scheduled":
        return (
            f"Auto scheduled propagation restart for {services}; "
            "safe window or I2 not yet clear."
        )
    return (
        f"Auto propagated restart for {services} — drain/restart submitted or deferred; "
        "ledger row open until proof closes."
    )


__all__ = ["run_propagation_in_seat"]
