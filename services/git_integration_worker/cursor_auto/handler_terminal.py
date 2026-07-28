"""Handler terminal helpers — journal + status posts (keeps handler.py lean)."""

from __future__ import annotations

import json
from typing import Any

from services.git_integration_worker.cursor_auto.queue import AutoJob
from services.git_integration_worker.cursor_auto.work_journal import (
    append_journal_entry,
)
from services.git_integration_worker.cursor_bus import CursorBusClient

_FROM_AUTO = "cursor-auto"


async def post_terminal_status(
    job: AutoJob,
    *,
    client: CursorBusClient,
    queue: Any,
    summary: str,
    disposition: str,
    payload: dict[str, Any],
    contract: str,
    terminal_status: str = "status:done",
    failed: bool = False,
    dispatch_id: str | None = None,
    journal_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Journal the episode and post one terminal ``status:*`` turn to the operator.

    The subject carries *terminal_status* verbatim, so waiters keyed on a
    completion token see exactly the vocabulary the caller chose.
    """
    extra: dict[str, Any] = {"summary": summary}
    if journal_extra:
        extra.update(journal_extra)
        if "summary" not in journal_extra:
            extra["summary"] = summary
    append_journal_entry(
        thread_id=job.thread_id,
        dispatch_id=dispatch_id,
        contract=contract,
        terminal_status=terminal_status,
        disposition=disposition,
        extra=extra,
    )
    terminal = await client.reply(
        thread_id=job.thread_id,
        to_agent=job.from_agent,
        from_agent=_FROM_AUTO,
        subject=f"{terminal_status} — {job.subject[:60]}",
        body=json.dumps(payload, indent=2),
        allow_long_body=True,
    )
    queue.mark_done(job.job_id, failed=failed)
    return {
        "ok": not failed and terminal.status_code < 400,
        "phase": "terminal",
        "terminal_status": terminal_status,
        "disposition": disposition,
        "status_code": terminal.status_code,
        "summary": summary,
    }


async def terminal_in_seat(
    job: AutoJob,
    *,
    client: CursorBusClient,
    queue: Any,
    model: dict[str, Any],
    effort: dict[str, Any],
    contract_info: dict[str, Any],
    gate_plan: dict[str, Any],
) -> dict[str, Any]:
    """Close a non-nested contract that Auto handled without an SDK dispatch."""
    summary = (
        f"v0 in-seat Auto handled contract={contract_info['contract']} "
        f"(gate_plan={gate_plan['action']})."
    )
    return await post_terminal_status(
        job,
        client=client,
        queue=queue,
        summary=summary,
        disposition=str(contract_info["disposition_hint"]),
        contract=job.contract,
        payload={
            "summary": summary,
            "disposition": contract_info["disposition_hint"],
            "requested_model": model["requested"],
            "actual_model": model["resolved_model_id"],
            "requested_effort": effort["requested"],
            "actual_effort": effort["resolved_effort"],
            "gate_plan": gate_plan,
            "request_turn": job.turn_number,
        },
    )


async def terminal_needs_attended(
    job: AutoJob,
    *,
    client: CursorBusClient,
    queue: Any,
    reason: str,
    gate_plan: dict[str, Any],
) -> dict[str, Any]:
    """Refuse a job that cannot run unattended in this seat."""
    summary = f"Auto cannot run unattended: {reason}"
    return await post_terminal_status(
        job,
        client=client,
        queue=queue,
        summary=summary,
        disposition="needs-attended",
        contract=job.contract,
        terminal_status="status:needs-attended",
        payload={"summary": summary, "reason": reason, "gate_plan": gate_plan},
        failed=True,
    )


async def terminal_failed(
    job: AutoJob,
    *,
    client: CursorBusClient,
    queue: Any,
    summary: str,
    extra: dict[str, Any],
    dispatch_id: str | None = None,
) -> dict[str, Any]:
    """Close a job whose nested dispatch could not be submitted or polled."""
    payload = {"summary": summary, **extra}
    if dispatch_id:
        payload["dispatch_id"] = dispatch_id
    return await post_terminal_status(
        job,
        client=client,
        queue=queue,
        summary=summary,
        disposition="blocked",
        contract=job.contract,
        terminal_status="status:failed",
        payload=payload,
        failed=True,
    )
