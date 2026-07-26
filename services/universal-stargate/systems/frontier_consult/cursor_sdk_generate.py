"""SDK-substrate generate dispatch — bypasses cloud CapabilityDispatch."""

from __future__ import annotations

from typing import Any, Literal

from agent_seat.profiles import get_profile

from .cursor_sdk_generate_prepare import (
    PreparedCursorSdkHandle,
    prepare_cursor_sdk_generate,
)
from .cursor_sdk_generate_signals import emit_sdk_worker_outcome
from .cursor_sdk_worker_dispatch import (
    dispatch_cursor_sdk_worker,
    dispatch_cursor_sdk_worker_message,
)
from .handoff_response import (
    build_handoff_result,
    build_sdk_generate_result,
    resolve_poll_wait_seconds,
)
from .poll_hint_events import emit_poll_hint_from_handoff

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


async def dispatch_prepared_cursor_sdk(
    handle: PreparedCursorSdkHandle,
) -> dict[str, Any]:
    """Submit a prepared handle to the worker without reminting identities."""
    if handle.thread_id is None:
        from .admission import FrontierEndpointError

        raise FrontierEndpointError(
            request_id=handle.request_id,
            field="thread_id",
            reason="prepared handle missing thread_id; materialize before worker POST",
            status_code=422,
            code="CURSOR_PREPARED_HANDLE_INCOMPLETE",
        )

    if handle.packet_path is not None:
        worker_ok, worker_detail = await dispatch_cursor_sdk_worker(
            request_id=handle.request_id,
            thread_id=handle.thread_id,
            model=handle.resolved_model,
            execution_id=handle.execution_id,
            packet_path=handle.packet_path,
            handoff_contract=handle.handoff_contract,
            caller_agent=handle.caller_agent,
            prompt_preamble=handle.prompt_preamble,
            model_knobs=handle.aligned_knobs,
            read_only=handle.read_only,
            dispatch_id=handle.dispatch_id,
            nest_under=handle.nest_under,
        )
    else:
        worker_ok, worker_detail = await dispatch_cursor_sdk_worker_message(
            request_id=handle.request_id,
            thread_id=handle.thread_id,
            model=handle.resolved_model,
            message=handle.message or "",
            execution_id=handle.execution_id,
            caller_agent=handle.caller_agent,
            model_knobs=handle.aligned_knobs,
            read_only=handle.read_only,
            dispatch_id=handle.dispatch_id,
            nest_under=handle.nest_under,
        )

    if not worker_ok:
        emit_sdk_worker_outcome(
            request_id=handle.request_id,
            thread_id=handle.thread_id,
            execution_id=handle.execution_id,
            worker_ok=False,
            worker_detail=worker_detail,
        )
        _worker_dispatch_error(request_id=handle.request_id, detail=worker_detail)

    queued = bool(worker_detail.get("queued"))
    emit_sdk_worker_outcome(
        request_id=handle.request_id,
        thread_id=handle.thread_id,
        execution_id=handle.execution_id,
        worker_ok=True,
        worker_detail=worker_detail,
    )

    profile = get_profile(handle.family, handle.platform)
    handoff_fields = build_handoff_result(
        thread_id=handle.thread_id,
        to_agent=handle.to_agent,
        reply_from_agent=CURSOR_SDK_REPLY_SEAT,
        poll_wait_seconds=resolve_poll_wait_seconds(poller_is_cursor_ide=True),
    )
    emit_poll_hint_from_handoff(
        request_id=handle.request_id,
        thread_id=handle.thread_id,
        caller_agent=handle.caller_agent or "cursor",
        handoff_fields=handoff_fields,
    )
    result = build_sdk_generate_result(
        role=handle.role,
        profile=profile,
        handoff_fields=handoff_fields,
        execution_id=handle.execution_id,
        thread_id=handle.thread_id,
        to_agent=handle.to_agent,
        resolved_model=handle.resolved_model,
        resolved_contract=handle.handoff_contract,
        warnings=list(handle.alignment_warnings),
        durable=handle.admitted,
        density_triage=handle.density_triage,
        review_opt_out_reason_code=handle.review_opt_out_reason_code,
        auto_review_child=handle.auto_review_child,
    )
    if handle.auto_review_defaulted:
        result["auto_review_defaulted"] = True
    result["knob_resolution"] = list(handle.knob_resolution)
    if queued:
        ticket = worker_detail.get("ticket") or {}
        result["status"] = "queued"
        result["queue_ticket"] = ticket
    result["dispatch_id"] = handle.dispatch_id
    result["request_id"] = handle.request_id
    return result


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
    dispatch_thread_id: str | None = None,
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
    max_tool_turns: int | None = None,
    prepared_handle: PreparedCursorSdkHandle | None = None,
    source_ref: str | None = None,
    dispatch_lane: str | None = None,
    nest_under: str | None = None,
) -> dict[str, Any]:
    """Execute cursor-sdk generate with to_thread default delivery.

    Packet present: worker gets ``packet_path=`` (durable instruction channel).
    No packet: dispatch-thread text → worker gets ``message=`` (bus-turn fallback).
    When ``prepared_handle`` is supplied, identities are not reminted.
    """
    if prepared_handle is not None:
        return await dispatch_prepared_cursor_sdk(prepared_handle)

    handle = await prepare_cursor_sdk_generate(
        request_id=request_id,
        role=role,
        model=model,
        subject=subject,
        caller_agent=caller_agent,
        contract=contract,
        packet_path=packet_path,
        message_text=message_text,
        reuse_thread=reuse_thread,
        bus_lifecycle=bus_lifecycle,
        parent_dispatch_thread_id=parent_dispatch_thread_id,
        dispatch_thread_id=dispatch_thread_id,
        is_auto_consolidation=is_auto_consolidation,
        density_triage=density_triage,
        review_opt_out_reason_code=review_opt_out_reason_code,
        auto_review_child=auto_review_child,
        read_only=read_only,
        model_knobs=model_knobs,
        cost_intent=cost_intent,
        suppress_cost_warning=suppress_cost_warning,
        cost_intent_reason=cost_intent_reason,
        reasoning_effort=reasoning_effort,
        max_tool_turns=max_tool_turns,
        source_ref=source_ref,
        dispatch_lane=dispatch_lane,
        nest_under=nest_under,
    )
    return await dispatch_prepared_cursor_sdk(handle)
