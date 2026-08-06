"""Handler terminal helpers — journal + status posts (keeps handler.py lean)."""

from __future__ import annotations

import json
from typing import Any

from agent_seat.registry import normalize_bus_address

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
    if job.request_id and "request_id" not in payload:
        payload = {**payload, "request_id": job.request_id}
    extra: dict[str, Any] = {"summary": summary, "request_id": job.request_id}
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
    # Row 21 / C3: cdp → web-anthropic so void notices reach a live mailbox.
    successor_mailbox = normalize_bus_address(job.from_agent)
    terminal = await client.reply(
        thread_id=job.thread_id,
        to_agent=successor_mailbox,
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


ANSWER_DECLINED_REASON = "answer_in_seat_no_execution"
ANSWER_ROUTING_HINT = (
    "The in-seat answer contract executes nothing. For execution re-issue with "
    "contract=implement and a scoped DIRECTIVE (scope: or tool_op: + "
    "effects_expected:, plus files_expected: and vision:); for service restart "
    "use contract=propagate (scope: propagation sync_restart <service> or "
    "## propagation YAML + effects_expected:); for advisory judgment use "
    "contract=confer."
)


async def terminal_in_seat(
    job: AutoJob,
    *,
    client: CursorBusClient,
    queue: Any,
    model: dict[str, Any],
    effort: dict[str, Any],
    contract_info: dict[str, Any],
    gate_plan: dict[str, Any],
    answer_body: str | None = None,
) -> dict[str, Any]:
    """Close a non-nested contract that Auto handled without an SDK dispatch.

    ``answer`` executes nothing in seat, so unless *answer_body* carries
    substantive content the terminal declines with a routing hint instead of
    reporting a success-shaped no-op (``status:done`` stays the completion
    token so waiters still resolve, but the disposition tells the truth).
    """
    contract = str(contract_info["contract"])
    disposition = str(contract_info["disposition_hint"])
    body_text = (answer_body or "").strip()
    declined = contract == "answer" and not body_text
    if declined:
        disposition = "declined"
        summary = (
            f"Auto declined in-seat contract={contract} — no work executed "
            f"({ANSWER_DECLINED_REASON})."
        )
    else:
        summary = (
            f"v0 in-seat Auto handled contract={contract} "
            f"(gate_plan={gate_plan['action']})."
        )
    payload: dict[str, Any] = {
        "summary": summary,
        "disposition": disposition,
        "disposition_hint": contract_info["disposition_hint"],
        "requested_model": model["requested"],
        "actual_model": model["resolved_model_id"],
        "requested_effort": effort["requested"],
        "actual_effort": effort["resolved_effort"],
        "gate_plan": gate_plan,
        "request_turn": job.turn_number,
    }
    if declined:
        payload["declined_reason"] = ANSWER_DECLINED_REASON
        payload["routing_hint"] = ANSWER_ROUTING_HINT
    elif body_text:
        payload["answer_body"] = body_text
    return await post_terminal_status(
        job,
        client=client,
        queue=queue,
        summary=summary,
        disposition=disposition,
        contract=job.contract,
        payload=payload,
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
    payload: dict[str, Any] = {
        "summary": summary,
        "reason": reason,
        "gate_plan": gate_plan,
        "request_turn": job.turn_number,
    }
    if job.request_id:
        payload["request_id"] = job.request_id
    return await post_terminal_status(
        job,
        client=client,
        queue=queue,
        summary=summary,
        disposition="needs-attended",
        contract=job.contract,
        terminal_status="status:needs-attended",
        payload=payload,
        failed=True,
    )


async def terminal_expired(
    job: AutoJob,
    *,
    client: CursorBusClient,
    queue: Any,
    deadline: str | None = None,
    elapsed_s: float = 0.0,
) -> dict[str, Any]:
    """Terminate a job past its DIRECTIVE ``deadline:`` before any execution."""
    summary = (
        f"Job expired before execution — deadline {deadline!r} exceeded after "
        f"{elapsed_s / 60:.1f} min queued; stale intent was not run."
    )
    payload: dict[str, Any] = {
        "summary": summary,
        "reason": "expired",
        "deadline": deadline,
        "elapsed_s": round(elapsed_s, 1),
        "request_turn": job.turn_number,
    }
    if job.request_id:
        payload["request_id"] = job.request_id
    return await post_terminal_status(
        job,
        client=client,
        queue=queue,
        summary=summary,
        disposition="expired",
        contract=job.contract,
        terminal_status="status:failed",
        payload=payload,
        failed=True,
    )


async def post_queue_owner_restart_terminal(
    job: AutoJob,
    *,
    client: CursorBusClient,
    queue: Any,
) -> dict[str, Any]:
    """Notify waiters that the queue owner restarted before this job finished."""
    summary = (
        "Auto job lost when git_integration_worker restarted "
        "(dead_on_giw_restart); re-issue the DIRECTIVE."
    )
    payload: dict[str, Any] = {
        "summary": summary,
        "reason": "queue_owner_restart",
        "legacy_reason": "dead_on_giw_restart",
        "job_id": job.job_id,
        "request_turn": job.turn_number,
    }
    if job.request_id:
        payload["request_id"] = job.request_id
    return await post_terminal_status(
        job,
        client=client,
        queue=queue,
        summary=summary,
        disposition="failed",
        contract=job.contract,
        terminal_status="status:failed",
        payload=payload,
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
