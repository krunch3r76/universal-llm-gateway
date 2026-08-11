"""Pre-nest admit gates — relay trust, synthesized closeouts, auth-gate budget.

Gates refuse the job before any nested SDK capacity is spent, so a thread
whose history cannot be verified never reaches ``submit_nested_dispatch``.
"""

from __future__ import annotations

from typing import Any

from claim_register import claimed_derived

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
    is_continuity_hop_request,
    parse_request_body,
)
from services.git_integration_worker.cursor_auto.dispatch_bounds import (
    scope_waiver_allowed,
)
from services.git_integration_worker.cursor_auto.episode_briefing import (
    fetch_thread_status,
    fetch_thread_turns,
)
from services.git_integration_worker.cursor_auto.execute_admission import (
    EXECUTE_CONTRACT,
    admit_execute_body,
)
from services.git_integration_worker.cursor_auto.execute_events import (
    emit_execute_admission_blocked,
)
from services.git_integration_worker.cursor_auto.fix_hints import (
    CONTINUITY_HOP_FIX_HINT,
    EMPTY_SCOPE_FIX_HINT,
    MISSION_CLOSE_WAKE_FIX_HINT,
    OPTIONS_SYMMETRY_FIX_HINT,
    PROPAGATE_MISSING_FIX_HINT,
    VISION_MISSING_FIX_HINT,
)
from services.git_integration_worker.cursor_auto.handler_terminal import (
    post_terminal_status,
)
from services.git_integration_worker.cursor_auto.options_admission import (
    admit_options_body,
)
from services.git_integration_worker.cursor_auto.propagate_admission import (
    PROPAGATE_CONTRACT,
    admit_propagate_body,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob
from services.git_integration_worker.cursor_auto.relay_trust import (
    pending_synthesized_closeout,
)
from services.git_integration_worker.cursor_auto.wire_map import resolve_desired_model
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
    from claude_bundles.mission_close_wake import validate_mission_close_wake

    # F5 defense: hops must not enter implement admit. Short-circuit in
    # process_job is primary; if we still land here, refuse with a hint that
    # does NOT counsel adding vision:/scope: (that deepens the misroute).
    is_hop, _token = is_continuity_hop_request(
        job.body, wire_flag=bool(job.continuity_hop)
    )
    if is_hop or job.continuity_hop:
        summary = (
            "Continuity hop reached implement admit — routing defect "
            "(continuity_hop_misroute). Do not add vision/scope fields."
        )
        return await _blocked(
            job,
            client=client,
            queue=queue,
            summary=summary,
            payload={
                "summary": summary,
                "reason": "continuity_hop_misroute",
                "fix_hint": CONTINUITY_HOP_FIX_HINT,
            },
        )

    wake = validate_mission_close_wake(subject=job.subject or "", body=job.body or "")
    if not wake.ok:
        summary = (
            "Mission close refused — outstanding work has no named wake path "
            f"({wake.reason or 'mission_close_wake_path_missing'})."
        )
        return await _blocked(
            job,
            client=client,
            queue=queue,
            summary=summary,
            payload={
                "summary": summary,
                "reason": wake.reason or "mission_close_wake_path_missing",
                "missed_tokens": list(wake.missed_tokens),
                "fix_hint": MISSION_CLOSE_WAKE_FIX_HINT,
            },
        )

    from claude_bundles.pickup_awaits import (
        PICKUP_AWAITS_STOP_FIX_HINT,
        PICKUP_DECLARATION_FIX_HINT,
        validate_pickup_awaits,
    )

    pickup = validate_pickup_awaits(
        subject=job.subject or "",
        body=job.body or "",
        prior_turns=None,
    )
    if not pickup.ok:
        reason = pickup.reason or "pickup_declaration_missing"
        summary = f"Pickup/awaits gate refused ({reason})."
        hint = (
            PICKUP_AWAITS_STOP_FIX_HINT
            if reason == "pickup_awaits_unbound"
            else PICKUP_DECLARATION_FIX_HINT
        )
        return await _blocked(
            job,
            client=client,
            queue=queue,
            summary=summary,
            payload={
                "summary": summary,
                "reason": reason,
                "missed_tokens": list(pickup.missed_tokens),
                "fix_hint": hint,
            },
        )

    contract = (job.contract or "answer").strip().lower()
    if contract == EXECUTE_CONTRACT:
        admission = admit_execute_body(job.body)
        if admission.approved:
            return None
        error = admission.error or {"reason": "execute_admission_refused"}
        summary = str(error.get("summary", "execute admission refused"))
        emit_execute_admission_blocked(
            thread_id=job.thread_id,
            reason=str(error.get("reason", "execute_admission_refused")),
            tool_op=error.get("tool_op"),
        )
        return await _blocked(
            job,
            client=client,
            queue=queue,
            summary=summary,
            payload={**error, "summary": summary, "contract": contract},
        )
    if contract == PROPAGATE_CONTRACT:
        admission = admit_propagate_body(job.body)
        if admission.approved:
            return None
        error = admission.error or {"reason": "propagate_admission_refused"}
        summary = str(error.get("summary", "propagate admission refused"))
        return await _blocked(
            job,
            client=client,
            queue=queue,
            summary=summary,
            payload={
                **error,
                "summary": summary,
                "contract": contract,
                "fix_hint": error.get("fix_hint", PROPAGATE_MISSING_FIX_HINT),
            },
        )
    if contract in NESTED_SCOPE_CONTRACTS and not has_actionable_scope(job.body):
        directive = parse_request_body(job.body)
        density = directive.density if directive is not None else None
        resolved_model_id = str(
            resolve_desired_model(job.desired_model, contract=contract).get(
                "resolved_model_id"
            )
            or ""
        )
        override = body_has_contract_override(job.body)
        if override and scope_waiver_allowed(resolved_model_id):
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
            if override:
                summary = (
                    f"Empty directive scope — {resolved_model_id} is outside the "
                    "roaming tier, so a contract override does not waive the scope "
                    "bound (empty_directive_scope). Bound the work or bind "
                    "composer-2.5/grok-4.5."
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
                    "resolved_model": resolved_model_id,
                    "scope_waiver_withheld": override,
                    "fix_hint": EMPTY_SCOPE_FIX_HINT,
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
                    # reason = observed gate identity (which gate fired).
                    "reason": "vision_field_missing",
                    "contract": contract,
                    "density": density,
                    # fix_hint = derived counsel (row 29 member 4 proof slice).
                    "fix_hint": claimed_derived(
                        VISION_MISSING_FIX_HINT,
                        basis="admit_gates.vision_field_missing",
                    ).to_wire(),
                },
            )
        admission = admit_options_body(job.body)
        if not admission.approved and admission.error is not None:
            summary = admission.error["summary"]
            return await _blocked(
                job,
                client=client,
                queue=queue,
                summary=summary,
                payload={
                    **admission.error,
                    "contract": contract,
                    "density": directive.density,
                    "fix_hint": claimed_derived(
                        admission.error.get("fix_hint", OPTIONS_SYMMETRY_FIX_HINT),
                        basis=f"admit_gates.{admission.error['reason']}",
                    ).to_wire(),
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
            "Relay trust gate cannot verify thread history (relay_trust_unverifiable)."
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
