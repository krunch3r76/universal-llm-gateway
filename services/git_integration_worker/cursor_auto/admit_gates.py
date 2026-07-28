"""Pre-nest admit gates — relay trust, synthesized closeouts, auth-gate budget.

Gates refuse the job before any nested SDK capacity is spent, so a thread
whose history cannot be verified never reaches ``submit_nested_dispatch``.
"""

from __future__ import annotations

from typing import Any

from services.git_integration_worker.cursor_auto.auth_gate_budget import (
    count_auth_gate_failures,
    effective_auth_gate_budget,
    pending_auth_gate_block,
)
from services.git_integration_worker.cursor_auto.directive import (
    NESTED_SCOPE_CONTRACTS,
    VISION_REQUIRED_CONTRACTS,
    body_has_contract_override,
    empty_directive_missed_tokens,
    has_actionable_scope,
    has_vision_field,
    parse_request_body,
)
from services.git_integration_worker.cursor_auto.episode_briefing import (
    fetch_thread_status,
    fetch_thread_turns,
)
from services.git_integration_worker.cursor_auto.handler_terminal import (
    post_terminal_status,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob
from services.git_integration_worker.cursor_auto.relay_trust import (
    pending_synthesized_closeout,
)
from services.git_integration_worker.cursor_bus import CursorBusClient
from services.git_integration_worker.cursor_sdk_events import (
    emit_frontier_sdk_auto_auth_gate_blocked,
    emit_frontier_sdk_auto_empty_directive_scope_blocked,
    emit_frontier_sdk_auto_empty_directive_scope_waived,
    emit_frontier_sdk_auto_thread_status_refused,
)


async def blocking_admit_gate(
    job: AutoJob,
    *,
    client: CursorBusClient,
    queue: Any,
) -> dict[str, Any] | None:
    """Return a terminal ``status:blocked`` result when an admit gate refuses.

    ``None`` means all gates passed and the caller may continue to nest.
    """
    contract = (job.contract or "answer").strip().lower()
    if contract in NESTED_SCOPE_CONTRACTS and not has_actionable_scope(job.body):
        directive = parse_request_body(job.body)
        density = directive.density if directive is not None else None
        if body_has_contract_override(job.body):
            emit_frontier_sdk_auto_empty_directive_scope_waived(
                thread_id=job.thread_id,
                contract=contract,
            )
        else:
            missed = empty_directive_missed_tokens(job.body)
            summary = (
                "Empty directive scope — no actionable scope/todo/packet/"
                "files_expected (empty_directive_scope)."
            )
            emit_frontier_sdk_auto_empty_directive_scope_blocked(
                thread_id=job.thread_id,
                contract=contract,
                density=density,
                missed_tokens=missed,
            )
            return await _blocked(
                job,
                client=client,
                queue=queue,
                summary=summary,
                payload={
                    "summary": summary,
                    "reason": "empty_directive_scope",
                    "contract": contract,
                    "density": density,
                    "missed_tokens": list(missed),
                },
            )
    directive = parse_request_body(job.body)
    if contract in VISION_REQUIRED_CONTRACTS and directive is not None:
        if not has_vision_field(job.body):
            density = directive.density
            summary = (
                "Directive vision field missing — implement/investigate DIRECTIVEs "
                "require a vision: line (vision_field_missing)."
            )
            return await _blocked(
                job,
                client=client,
                queue=queue,
                summary=summary,
                payload={
                    "summary": summary,
                    "reason": "vision_field_missing",
                    "contract": contract,
                    "density": density,
                },
            )
    status = await fetch_thread_status(job.thread_id)
    if status in {"closed", "blocked"}:
        emit_frontier_sdk_auto_thread_status_refused(
            thread_id=job.thread_id,
            status=status,
        )
        summary = (
            f"Thread status {status} — refuse nest (thread_terminal_status_refused)."
        )
        return await _blocked(
            job,
            client=client,
            queue=queue,
            summary=summary,
            payload={
                "summary": summary,
                "reason": "thread_terminal_status_refused",
                "thread_status": status,
            },
        )
    turns = await fetch_thread_turns(job.thread_id)
    if turns is None:
        summary = (
            "Relay trust gate cannot verify thread history "
            "(relay_trust_unverifiable)."
        )
        return await _blocked(
            job,
            client=client,
            queue=queue,
            summary=summary,
            payload={"summary": summary, "relay_trust_unverifiable": True},
        )
    pending = pending_synthesized_closeout(turns, operator_from=job.from_agent)
    if pending:
        summary = (
            f"Synthesized closeout {pending} awaits operator ack "
            "(synthesized_closeout_ack: <dispatch_id>)."
        )
        return await _blocked(
            job,
            client=client,
            queue=queue,
            summary=summary,
            payload={"summary": summary, "pending_synthesized_closeout": pending},
        )
    if pending_auth_gate_block(turns, operator_from=job.from_agent):
        failures = count_auth_gate_failures(
            turns, operator_from=job.from_agent
        )
        budget, post_ack = effective_auth_gate_budget(
            turns, operator_from=job.from_agent
        )
        summary = (
            "auth_gate_budget_exhausted — "
            f"{failures} classified auth-gate CLOSEOUTs "
            f"(budget={budget}, post_ack={post_ack}). "
            "Post auth_gate_ack: <thread_id|dispatch_id> then confer."
        )
        emit_frontier_sdk_auto_auth_gate_blocked(
            thread_id=job.thread_id,
            failure_count=failures,
            budget=budget,
            post_ack=post_ack,
        )
        return await _blocked(
            job,
            client=client,
            queue=queue,
            summary=summary,
            payload={
                "summary": summary,
                "reason": "auth_gate_budget_exhausted",
                "gate_class": "auth_gate",
                "failures": failures,
                "budget": budget,
                "post_ack": post_ack,
                "scope": f"thread:{job.thread_id}",
                "recommended_next": (
                    "contract:confer — ask cursor/grok-4.5 or CDP Opus whether "
                    "auth path is automatable; else operator human gate"
                ),
            },
            journal_extra={
                "gate_class": "auth_gate",
                "summary": summary,
                "budget": budget,
                "post_ack": post_ack,
            },
        )
    return None


async def _blocked(
    job: AutoJob,
    *,
    client: CursorBusClient,
    queue: Any,
    summary: str,
    payload: dict[str, Any],
    journal_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await post_terminal_status(
        job,
        client=client,
        queue=queue,
        summary=summary,
        disposition="blocked",
        contract=job.contract,
        terminal_status="status:blocked",
        payload=payload,
        failed=True,
        journal_extra=journal_extra,
    )
