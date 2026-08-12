"""Handler terminal helpers — journal + status posts (keeps handler.py lean)."""

from __future__ import annotations

import json
from typing import Any

from agent_seat.registry import normalize_bus_address
from claim_register import normalize_claim_bearing_payload

from services.git_integration_worker.cursor_auto.queue import AutoJob
from services.git_integration_worker.cursor_auto.status_token_register import (
    stamp_disposition_hint_status_of,
    stamp_disposition_status_of,
    stamp_terminal_status_status_of,
    strip_disposition_hint_status_of,
    strip_disposition_status_of,
)
from services.git_integration_worker.cursor_auto.terminal_post_outcome import (
    terminal_post_delivered,
    terminal_post_retryable,
    terminal_reason_for_status,
)
from services.git_integration_worker.cursor_auto.terminal_reason_codec import (
    deliberate_failure_terminal_reason,
)
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
    disposition: str | None,
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

    *disposition* may be None when path-gated outcome tokens must omit (arc
    6655); the journal key and return dict then omit ``disposition``.

    Claim-register partial guard (row 29): claim-bearing keys in *payload*
    (``fix_hint``, ``claim_register``) are normalized via
    :func:`claim_register.normalize_claim_bearing_payload` before dump.
    Missing registers are stamped ``unknown`` and the turn still posts —
    never fail-closed here (a dropped closeout is worse than an untyped
    claim that announces itself). Fail-closed lives on ``Claimed``
    construction and in unit tests only.

    NAMED ABSENCE: this chokepoint does **not** cover member 1 (RESIDUE
    markdown), member 5 (``Verification`` packers), or member 6 (authoring /
    mission-close — Packet E: skill + ``MISSION_SKILL_SLUGS`` chip, not this
    wire). Member 2 (ledger ``status``) closed Packet D via
    ``propagation_attempt_status`` + ``observe_code_ref_live`` — not here.
    See ``claim_register.wire`` docstring.

    When ``disposition_hint`` is present, emission adds sibling
    ``disposition_hint_status_of`` naming the *planned* contract-policy register
    (admit-time; may diverge from observed ``disposition``).

    When ``disposition`` is present, emission adds sibling
    ``disposition_status_of`` naming the *observed* outcome register without
    renaming the bare token (arc 6655 rank-(i) additive slice).

    Every emission also adds ``terminal_status_status_of`` naming what register
    the wait-subject ``status:*`` token belongs to (arc 6655 rank-1b) — subject
    line unchanged so wait predicates and prefix parsers keep matching.
    """
    if job.request_id and "request_id" not in payload:
        payload = {**payload, "request_id": job.request_id}
    # Partial guard — degrade, do not refuse (Packet A bind).
    payload = normalize_claim_bearing_payload(payload)
    if "disposition_hint" in payload:
        payload = stamp_disposition_hint_status_of(payload)
    else:
        payload = strip_disposition_hint_status_of(payload)
    if disposition is None:
        payload = {k: v for k, v in payload.items() if k != "disposition"}
        payload = strip_disposition_status_of(payload)
    elif "disposition" not in payload:
        payload = {**payload, "disposition": disposition}
        payload = stamp_disposition_status_of(payload)
    else:
        payload = stamp_disposition_status_of(payload)
    payload = stamp_terminal_status_status_of(payload)
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
        allow_long_body=False,
    )
    delivered = terminal_post_delivered(terminal.status_code)
    if delivered:
        term_reason = None
        if failed:
            term_reason = deliberate_failure_terminal_reason(
                disposition=disposition,
                payload=payload,
                summary=summary,
            )
        queue.mark_done(job.job_id, failed=failed, terminal_reason=term_reason)
    else:
        queue.mark_report_undelivered(
            job.job_id,
            terminal_reason=terminal_reason_for_status(terminal.status_code),
            retryable=terminal_post_retryable(terminal.status_code),
            status_code=terminal.status_code,
        )
    out: dict[str, Any] = {
        "ok": not failed and delivered,
        "delivered": delivered,
        "phase": "terminal",
        "terminal_status": terminal_status,
        "status_code": terminal.status_code,
        "summary": summary,
    }
    if disposition is not None:
        out["disposition"] = disposition
    # Surface join keys for hop-cadence succession (payload-only was invisible).
    for key in ("execution_id", "satellite_execution_id"):
        if payload.get(key):
            out[key] = payload[key]
    return out


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

    In-seat paths never satisfy nested/CDP M1, so outcome token
    ``dispatched-and-relayed`` (e.g. seed hint) is omitted from reader fields.
    """
    from services.git_integration_worker.cursor_auto.disposition_outcome import (
        outcome_disposition_for_stamp,
    )

    contract = str(contract_info["contract"])
    disposition: str | None = outcome_disposition_for_stamp(
        str(contract_info["disposition_hint"]),
        m1_satisfied=False,
    )
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
        "disposition_hint": contract_info["disposition_hint"],
        "requested_model": model["requested"],
        "actual_model": model["resolved_model_id"],
        "requested_effort": effort["requested"],
        "actual_effort": effort["resolved_effort"],
        "gate_plan": gate_plan,
        "request_turn": job.turn_number,
    }
    if disposition is not None:
        payload["disposition"] = disposition
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
