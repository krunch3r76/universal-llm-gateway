"""Pre-nest admit gates — relay trust, synthesized closeouts, auth-gate budget.

Gates refuse the job before any nested SDK capacity is spent, so a thread
whose history cannot be verified never reaches ``submit_nested_dispatch``.

The ladder's body-pure prefix lives in ``admission_verdict`` so ``/enqueue`` can
project a verdict for the caller; this module fires that verdict's emits and
owns the terminal post, then runs the thread-state gates that need I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.git_integration_worker.cursor_auto.admission_verdict import (
    OUTCOME_ADMITTED,
    BodyPureVerdict,
    evaluate_body_pure_gates,
)
from services.git_integration_worker.cursor_auto.auth_gate_budget import (
    count_auth_gate_failures,
    effective_auth_gate_budget,
    pending_auth_gate_block,
)
from services.git_integration_worker.cursor_auto.episode_briefing import (
    fetch_thread_status,
    fetch_thread_turns,
)
from services.git_integration_worker.cursor_auto.execute_admission import (
    ExecuteAdmission,
)
from services.git_integration_worker.cursor_auto.execute_events import (
    emit_execute_admission_blocked,
)
from services.git_integration_worker.cursor_auto.handler_terminal import (
    post_terminal_status,
)
from services.git_integration_worker.cursor_auto.propagate_admission import (
    PropagateAdmission,
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


@dataclass(frozen=True, slots=True)
class AdmitGateResult:
    """Outcome of ``blocking_admit_gate`` — block payload plus retained admissions."""

    blocked: dict[str, Any] | None = None
    propagate_admission: PropagateAdmission | None = None
    execute_admission: ExecuteAdmission | None = None


def _fire_pending_emits(verdict: BodyPureVerdict, *, thread_id: str) -> None:
    """Fire the advisory emits the pure evaluator named but did not send."""
    for pending in verdict.pending_emits:
        if pending.name == "execute_admission_blocked":
            emit_execute_admission_blocked(thread_id=thread_id, **pending.kwargs)
        elif pending.name == "empty_directive_scope_waived":
            emit_frontier_sdk_auto_empty_directive_scope_waived(
                thread_id=thread_id, **pending.kwargs
            )
        elif pending.name == "empty_directive_scope_blocked":
            emit_frontier_sdk_auto_empty_directive_scope_blocked(
                thread_id=thread_id, **pending.kwargs
            )


async def blocking_admit_gate(
    job: AutoJob,
    *,
    client: CursorBusClient,
    queue: Any,
) -> AdmitGateResult:
    """Return block payload when an admit gate refuses; else retained admissions.

    ``blocked`` set means the caller must terminalize; parity lives on the
    admission objects either way for render on success paths.
    """
    verdict = evaluate_body_pure_gates(
        subject=job.subject or "",
        body=job.body or "",
        contract=job.contract or "answer",
        desired_model=job.desired_model,
        continuity_hop=bool(job.continuity_hop),
    )
    _fire_pending_emits(verdict, thread_id=job.thread_id)
    if verdict.refusal is not None:
        return AdmitGateResult(
            blocked=await _blocked(
                job,
                client=client,
                queue=queue,
                summary=verdict.refusal.summary,
                payload=verdict.refusal.payload,
                journal_extra=verdict.refusal.journal_extra,
            ),
            propagate_admission=verdict.propagate_admission,
            execute_admission=verdict.execute_admission,
        )
    if verdict.outcome == OUTCOME_ADMITTED:
        # execute / propagate approval short-circuits the thread-state gates.
        return AdmitGateResult(
            propagate_admission=verdict.propagate_admission,
            execute_admission=verdict.execute_admission,
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
        return AdmitGateResult(
            blocked=await _blocked(
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
        )
    turns = await fetch_thread_turns(job.thread_id)
    if turns is None:
        summary = (
            "Relay trust gate cannot verify thread history (relay_trust_unverifiable)."
        )
        return AdmitGateResult(
            blocked=await _blocked(
                job,
                client=client,
                queue=queue,
                summary=summary,
                payload={"summary": summary, "relay_trust_unverifiable": True},
            )
        )
    pending = pending_synthesized_closeout(turns, operator_from=job.from_agent)
    if pending:
        summary = (
            f"Synthesized closeout {pending} awaits operator ack "
            "(synthesized_closeout_ack: <dispatch_id>)."
        )
        return AdmitGateResult(
            blocked=await _blocked(
                job,
                client=client,
                queue=queue,
                summary=summary,
                payload={"summary": summary, "pending_synthesized_closeout": pending},
            )
        )
    if pending_auth_gate_block(turns, operator_from=job.from_agent):
        failures = count_auth_gate_failures(turns, operator_from=job.from_agent)
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
        return AdmitGateResult(
            blocked=await _blocked(
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
                        "contract:confer — ask cursor/claude-sonnet-5 or CDP Opus "
                        "whether auth path is automatable; else operator human gate"
                    ),
                },
                journal_extra={
                    "gate_class": "auth_gate",
                    "summary": summary,
                    "budget": budget,
                    "post_ack": post_ack,
                },
            )
        )
    return AdmitGateResult()


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
