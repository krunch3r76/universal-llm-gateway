"""SDK-substrate generate dispatch — bypasses cloud CapabilityDispatch."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from agent_seat.profiles import get_profile

from .admission import resolve_cursor_sdk_generate_target
from .cursor_sdk_coord_notify import post_coord_admit_pointer
from .cursor_sdk_generate_signals import (
    emit_sdk_generate_requested,
    emit_sdk_thread_created,
    emit_sdk_worker_outcome,
)
from .cursor_sdk_worker_dispatch import (
    derive_cursor_sdk_prompt_preamble,
    dispatch_cursor_sdk_worker,
    dispatch_cursor_sdk_worker_message,
    post_worker_failure_turn,
)
from .handoff import admit_handoff_dispatch, create_handoff_thread
from .handoff_response import build_handoff_result, build_sdk_generate_result

CURSOR_SDK_REPLY_SEAT = "cursor-sdk"


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
    density_triage: str | None = None,
    review_opt_out_reason_code: str | None = None,
    auto_review_child: bool = False,
    read_only: bool = False,
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
    # Prong-1 close-on-read parity with API-role generate: persistent + type:generate
    # so spawned worker threads stay readable until the result turn is consumed.
    effective_bus_lifecycle: Literal["persistent", "ephemeral"] = (
        bus_lifecycle if bus_lifecycle is not None else "persistent"
    )
    coord_recipient = caller_agent or "dispatch"

    emit_sdk_generate_requested(
        request_id=request_id,
        role=role,
        execution_id=execution_id,
        handoff_contract=handoff_contract,
        resolved_model=resolved_model,
    )

    if reuse_thread is not None:
        from .handoff import post_pointer_turn

        thread_id = reuse_thread
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
        prompt_preamble = derive_cursor_sdk_prompt_preamble(
            handoff_contract=handoff_contract,
            pointer=preamble_pointer,
        )
        worker_ok, worker_warning = await dispatch_cursor_sdk_worker(
            request_id=request_id,
            thread_id=thread_id,
            model=resolved_model,
            execution_id=execution_id,
            packet_path=worker_packet,
            handoff_contract=handoff_contract,
            caller_agent=caller_agent,
            prompt_preamble=prompt_preamble,
            read_only=read_only,
        )
    else:
        worker_ok, worker_warning = await dispatch_cursor_sdk_worker_message(
            request_id=request_id,
            thread_id=thread_id,
            model=resolved_model,
            message=worker_message or "",
            execution_id=execution_id,
            caller_agent=caller_agent,
            read_only=read_only,
        )

    emit_sdk_worker_outcome(
        request_id=request_id,
        thread_id=thread_id,
        execution_id=execution_id,
        worker_ok=worker_ok,
        worker_warning=worker_warning,
    )

    warnings: list[str] = []
    if not worker_ok:
        await post_worker_failure_turn(
            thread_id=thread_id, request_id=request_id, to_agent=to_agent
        )
        if worker_warning:
            warnings.append(worker_warning)

    profile = get_profile(family, platform)
    handoff_fields = build_handoff_result(
        thread_id=thread_id,
        to_agent=to_agent,
        reply_from_agent=CURSOR_SDK_REPLY_SEAT,
    )
    return build_sdk_generate_result(
        role=role,
        profile=profile,
        handoff_fields=handoff_fields,
        execution_id=execution_id,
        thread_id=thread_id,
        to_agent=to_agent,
        resolved_model=resolved_model,
        resolved_contract=handoff_contract,
        warnings=warnings,
        durable=admitted,
        density_triage=density_triage,
        review_opt_out_reason_code=review_opt_out_reason_code,
        auto_review_child=auto_review_child,
    )
