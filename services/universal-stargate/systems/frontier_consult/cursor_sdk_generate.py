"""SDK-substrate generate dispatch — bypasses cloud CapabilityDispatch."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from agent_seat.profiles import get_profile

from .admission import resolve_cursor_sdk_generate_target
from .cursor_sdk_coord_notify import post_coord_admit_pointer
from .cursor_sdk_generate_signals import (
    emit_sdk_generate_requested,
    emit_sdk_materialization_incomplete,
    emit_sdk_thread_created,
    emit_sdk_worker_outcome,
)
from .cursor_sdk_worker_dispatch import (
    derive_cursor_sdk_prompt_preamble,
    dispatch_cursor_sdk_worker,
    dispatch_cursor_sdk_worker_message,
)
from .handoff import (
    PendingShellContention,
    admit_handoff_dispatch,
    claim_and_post_pointer_turn,
    create_handoff_thread,
    post_pointer_turn,
)
from .handoff_response import build_handoff_result, build_sdk_generate_result

CURSOR_SDK_REPLY_SEAT = "cursor-sdk"


def _worker_dispatch_error(
    *,
    request_id: str,
    detail: dict[str, Any],
) -> None:
    from .admission import FrontierEndpointError

    raise FrontierEndpointError(
        request_id=request_id,
        field="worker_dispatch",
        reason=str(detail.get("message") or "worker dispatch failed"),
        status_code=int(detail.get("status_code") or 502),
        code=str(detail.get("code") or "CURSOR_WORKER_DISPATCH_FAILED"),
        details={
            k: detail[k]
            for k in ("status_code", "code", "blocking_dispatch_id")
            if detail.get(k) is not None
        },
    )


async def dispatch_cursor_sdk_generate(
    *,
    request_id: str,
    role: str,
    model: str | None,
    subject: str | None,
    caller_agent: str | None,
    contract: Literal["light-bounded", "pure-mechanical", "implement"],
    packet_path: str | None,
    message_text: str | None,
    reuse_thread: str | None = None,
    bus_lifecycle: Literal["persistent", "ephemeral"] | None = None,
    parent_dispatch_thread_id: str | None = None,
    is_auto_consolidation: bool = False,
    density_triage: str | None = None,
    review_opt_out_reason_code: str | None = None,
    auto_review_child: bool = False,
    read_only: bool = False,
    model_knobs: dict[str, str] | None = None,
    cost_intent: Literal["deliberate_high_cost"] | None = None,
    suppress_cost_warning: bool = False,
    cost_intent_reason: str | None = None,
    reasoning_effort: str | None = None,
) -> dict[str, Any]:
    """Execute cursor-sdk generate with to_thread default delivery.

    Packet present: worker gets ``packet_path=`` (durable instruction channel).
    No packet: dispatch-thread text → worker gets ``message=`` (bus-turn fallback).
    """
    from .densify_triage import validate_generate_density_intake

    validate_generate_density_intake(
        request_id=request_id,
        contract=contract,
        density_triage=density_triage,
        review_opt_out_reason_code=review_opt_out_reason_code,
        auto_review_child=auto_review_child,
    )
    to_agent, family, platform, resolved_model = resolve_cursor_sdk_generate_target(
        role, model=model, request_id=request_id
    )
    execution_id = str(uuid.uuid4())
    from .cursor_sdk_alignment import align_cursor_knobs

    alignment = align_cursor_knobs(
        resolved_model=resolved_model,
        contract=contract,
        model_knobs=model_knobs,
        cost_intent=cost_intent,
        suppress_cost_warning=suppress_cost_warning,
        cost_intent_reason=cost_intent_reason,
        reasoning_effort=reasoning_effort,
        request_id=request_id,
        execution_id=execution_id,
    )
    aligned_knobs = alignment.aligned_knobs or None
    # Scope the bus recipient to this dispatch so `fetch-unread --to cursor-sdk`
    # returns empty and sibling dispatches cannot contaminate each other's inbox
    # (structurally safe at CURSOR_SDK_DISPATCH_CONCURRENCY>1).
    # Thread TAG remains family-level "cursor-sdk" — passed as tag_agent below.
    to_agent = f"cursor-sdk:dispatch:{execution_id}"
    thread_subject = subject or f"cursor-sdk generate — {execution_id[:8]}"

    if contract == "implement" and packet_path is None:
        from .admission import FrontierEndpointError

        raise FrontierEndpointError(
            request_id=request_id,
            field="packet_path",
            reason="contract=implement requires packet_path",
            status_code=422,
        )

    if read_only and contract == "implement":
        from .admission import FrontierEndpointError

        raise FrontierEndpointError(
            request_id=request_id,
            field="read_only",
            reason="read_only=true is incompatible with contract=implement",
            status_code=422,
        )

    if contract == "pure-mechanical" and packet_path is not None:
        from .admission import FrontierEndpointError

        raise FrontierEndpointError(
            request_id=request_id,
            field="packet_path",
            reason=(
                "contract=pure-mechanical is packet-free; use contract=implement "
                "or light-bounded for packet-based dispatches"
            ),
            status_code=422,
        )

    if packet_path is not None:
        pointer_body = f"SDK {contract} dispatch — see packet `{packet_path}`."
        worker_packet = packet_path
        worker_message = None
    else:
        last_user = message_text or ""
        if not last_user:
            from .admission import FrontierEndpointError

            raise FrontierEndpointError(
                request_id=request_id,
                field="dispatch_thread_id",
                reason="Dispatch thread must contain a non-empty prompt body",
                status_code=422,
            )
        pointer_body = last_user[:2000]
        worker_packet = None
        worker_message = last_user

    handoff_contract = contract
    # Default ephemeral so dispatch-terminate auto-closes after closeout delivery;
    # explicit bus_lifecycle=persistent opts into close-on-read instead.
    effective_bus_lifecycle: Literal["persistent", "ephemeral"] = (
        bus_lifecycle if bus_lifecycle is not None else "ephemeral"
    )
    coord_recipient = caller_agent or "dispatch"

    emit_sdk_generate_requested(
        request_id=request_id,
        role=role,
        execution_id=execution_id,
        handoff_contract=handoff_contract,
        resolved_model=resolved_model,
    )

    claimed_via_atomic = False

    if reuse_thread is not None:
        thread_id = reuse_thread
        if is_auto_consolidation:
            try:
                await claim_and_post_pointer_turn(
                    request_id=request_id,
                    thread_id=thread_id,
                    to_agent=to_agent,
                    subject=thread_subject,
                    pointer_body=pointer_body,
                    caller_agent=caller_agent,
                    execution_id=execution_id,
                    pipeline_id="cursor-sdk-generate",
                )
                claimed_via_atomic = True
                emit_sdk_thread_created(
                    request_id=request_id,
                    to_agent=to_agent,
                    thread_id=thread_id,
                    reused=True,
                )
            except PendingShellContention:
                # Concurrent dispatch claimed the shell; mint a new worker thread.
                thread_id = await create_handoff_thread(
                    request_id=request_id,
                    to_agent=to_agent,
                    tag_agent="cursor-sdk",
                    subject=thread_subject,
                    pointer_body=pointer_body,
                    caller_agent=caller_agent,
                    tags=[
                        "cursor-sdk-generate",
                        "type:generate",
                        f"contract:{handoff_contract}",
                    ],
                    handoff_contract=handoff_contract,
                    lifecycle_state="pending",
                    bus_lifecycle=effective_bus_lifecycle,
                )
                emit_sdk_thread_created(
                    request_id=request_id,
                    to_agent=to_agent,
                    thread_id=thread_id,
                    reused=False,
                )
        else:
            await post_pointer_turn(
                request_id=request_id,
                thread_id=thread_id,
                to_agent=to_agent,
                subject=thread_subject,
                pointer_body=pointer_body,
                caller_agent=caller_agent,
            )
            emit_sdk_thread_created(
                request_id=request_id,
                to_agent=to_agent,
                thread_id=thread_id,
                reused=True,
            )
    else:
        thread_id = await create_handoff_thread(
            request_id=request_id,
            to_agent=to_agent,
            tag_agent="cursor-sdk",
            subject=thread_subject,
            pointer_body=pointer_body,
            caller_agent=caller_agent,
            tags=[
                "cursor-sdk-generate",
                "type:generate",
                f"contract:{handoff_contract}",
            ],
            handoff_contract=handoff_contract,
            lifecycle_state="pending",
            bus_lifecycle=effective_bus_lifecycle,
        )
        emit_sdk_thread_created(
            request_id=request_id,
            to_agent=to_agent,
            thread_id=thread_id,
            reused=False,
        )

    await post_coord_admit_pointer(
        coord_thread_id=parent_dispatch_thread_id,
        worker_thread_id=thread_id,
        to_agent=coord_recipient,
        caller_agent=caller_agent,
        contract=handoff_contract,
    )

    if claimed_via_atomic:
        admitted = True
    else:
        admitted = await admit_handoff_dispatch(
            request_id=request_id,
            thread_id=thread_id,
            execution_id=execution_id,
            pipeline_id="cursor-sdk-generate",
            caller_agent=caller_agent,
        )

    if worker_packet is not None:
        preamble_pointer = pointer_body
        if handoff_contract == "implement" and "Contract:" not in pointer_body:
            preamble_pointer = f"Contract: implement.\n{pointer_body}"
        packet_text = ""
        from .handoff import _resolve_packet_file, _workspaces_root

        packet_file = _resolve_packet_file(_workspaces_root().resolve(), worker_packet)
        if worker_packet is not None and packet_file is None:
            from .admission import FrontierEndpointError

            probe_root = str(_workspaces_root().resolve())
            emit_sdk_materialization_incomplete(
                request_id=request_id,
                packet_path=worker_packet,
                probe_root=probe_root,
                source_ref=worker_packet,
                execution_id=execution_id,
                thread_id=thread_id,
            )
            raise FrontierEndpointError(
                request_id=request_id,
                field="packet_path",
                reason="materialized packet absent at executor root",
                status_code=422,
                code="CURSOR_MATERIALIZATION_INCOMPLETE",
            )
        if packet_file is not None:
            packet_text = packet_file.read_text(encoding="utf-8", errors="replace")
        prompt_preamble = derive_cursor_sdk_prompt_preamble(
            handoff_contract=handoff_contract,
            pointer=preamble_pointer,
            packet_text=packet_text,
        )
        worker_ok, worker_detail = await dispatch_cursor_sdk_worker(
            request_id=request_id,
            thread_id=thread_id,
            model=resolved_model,
            execution_id=execution_id,
            packet_path=worker_packet,
            handoff_contract=handoff_contract,
            caller_agent=caller_agent,
            prompt_preamble=prompt_preamble,
            model_knobs=aligned_knobs,
            read_only=read_only,
        )
    else:
        worker_ok, worker_detail = await dispatch_cursor_sdk_worker_message(
            request_id=request_id,
            thread_id=thread_id,
            model=resolved_model,
            message=worker_message or "",
            execution_id=execution_id,
            caller_agent=caller_agent,
            model_knobs=aligned_knobs,
            read_only=read_only,
        )

    if not worker_ok:
        emit_sdk_worker_outcome(
            request_id=request_id,
            thread_id=thread_id,
            execution_id=execution_id,
            worker_ok=False,
            worker_detail=worker_detail,
        )
        _worker_dispatch_error(request_id=request_id, detail=worker_detail)

    queued = bool(worker_detail.get("queued"))
    emit_sdk_worker_outcome(
        request_id=request_id,
        thread_id=thread_id,
        execution_id=execution_id,
        worker_ok=True,
        worker_detail=worker_detail,
    )

    profile = get_profile(family, platform)
    handoff_fields = build_handoff_result(
        thread_id=thread_id,
        to_agent=to_agent,
        reply_from_agent=CURSOR_SDK_REPLY_SEAT,
    )
    result = build_sdk_generate_result(
        role=role,
        profile=profile,
        handoff_fields=handoff_fields,
        execution_id=execution_id,
        thread_id=thread_id,
        to_agent=to_agent,
        resolved_model=resolved_model,
        resolved_contract=handoff_contract,
        warnings=alignment.warnings_as_dicts(),
        durable=admitted,
        density_triage=density_triage,
        review_opt_out_reason_code=review_opt_out_reason_code,
        auto_review_child=auto_review_child,
    )
    result["knob_resolution"] = alignment.knob_resolution_as_dicts()
    if queued:
        ticket = worker_detail.get("ticket") or {}
        result["status"] = "queued"
        result["queue_ticket"] = ticket
    return result
