"""Terminal path for ``contract: execute`` — run one tier-M op, relay the payload.

Keeps ``handler.py`` free of Option A branching. The closeout carries the raw
tool payload inline: a codeblind operator must be able to disposition the episode
from the bus turn alone (Fable invariant 4).
"""

from __future__ import annotations

from typing import Any

from services.git_integration_worker.cursor_auto.execute_admission import (
    admit_execute_body,
)
from services.git_integration_worker.cursor_auto.execute_events import (
    emit_execute_op_ran,
)
from services.git_integration_worker.cursor_auto.execute_runner import (
    INVOKER_UNCONFIGURED_REASON,
    run_tool_op,
)
from services.git_integration_worker.cursor_auto.handler_terminal import (
    post_terminal_status,
    terminal_needs_attended,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob
from services.git_integration_worker.cursor_bus import CursorBusClient


async def run_execute_in_seat(
    job: AutoJob,
    *,
    client: CursorBusClient,
    queue: Any,
    model: dict[str, Any],
    effort: dict[str, Any],
    gate_plan: dict[str, Any],
) -> dict[str, Any]:
    """Fire the admitted single tier-M op in seat and post its terminal turn."""
    admission = admit_execute_body(job.body)
    if not admission.approved or admission.row is None:
        # The admit gate refuses these first; reaching here means the gate was
        # bypassed, so refuse rather than run an unvetted op.
        error = admission.error or {"reason": "execute_admission_missing"}
        summary = str(error.get("summary", "execute admission failed"))
        return await post_terminal_status(
            job,
            client=client,
            queue=queue,
            summary=summary,
            disposition="blocked",
            contract=job.contract,
            terminal_status="status:blocked",
            payload={"summary": summary, **error},
            failed=True,
        )

    row = admission.row
    outcome = await run_tool_op(row, admission.arguments)
    emit_execute_op_ran(
        thread_id=job.thread_id,
        tool_op=row.tool_op,
        idempotence=row.idempotence,
        ok=outcome.ok,
    )
    if not outcome.ok and outcome.reason == INVOKER_UNCONFIGURED_REASON:
        return await terminal_needs_attended(
            job,
            client=client,
            queue=queue,
            reason=INVOKER_UNCONFIGURED_REASON,
            gate_plan=gate_plan,
        )

    base: dict[str, Any] = {
        "tool_op": row.tool_op,
        "idempotence": row.idempotence,
        "tool_args": admission.arguments,
        "requested_model": model["requested"],
        "requested_effort": effort["requested"],
        "gate_plan": gate_plan,
        "request_turn": job.turn_number,
    }
    if not outcome.ok:
        summary = (
            f"Auto could not execute {row.tool_op} in seat "
            f"({outcome.reason}) — nothing ran."
        )
        return await post_terminal_status(
            job,
            client=client,
            queue=queue,
            summary=summary,
            disposition="failed",
            contract=job.contract,
            terminal_status="status:failed",
            payload={
                "summary": summary,
                "reason": outcome.reason,
                "error": outcome.error,
                **base,
            },
            failed=True,
        )

    summary = f"Auto executed {row.tool_op} in seat; raw payload relayed inline."
    return await post_terminal_status(
        job,
        client=client,
        queue=queue,
        summary=summary,
        disposition="executed",
        contract=job.contract,
        payload={
            "summary": summary,
            "disposition": "executed",
            "tool_payload": outcome.payload,
            **base,
        },
        journal_extra={"tool_op": row.tool_op, "idempotence": row.idempotence},
    )


__all__ = ["run_execute_in_seat"]
