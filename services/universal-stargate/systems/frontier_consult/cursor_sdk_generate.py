"""SDK-substrate generate dispatch — bypasses cloud CapabilityDispatch."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from agent_seat.profiles import get_profile

from .admission import resolve_cursor_sdk_generate_target
from .cursor_sdk_generate_signals import (
    emit_sdk_generate_requested,
    emit_sdk_thread_created,
    emit_sdk_worker_outcome,
)
from .cursor_sdk_worker_dispatch import (
    dispatch_cursor_sdk_worker,
    dispatch_cursor_sdk_worker_message,
    post_worker_failure_turn,
)
from .dispatch_messages import extract_last_user_message
from .handoff import admit_handoff_dispatch, create_handoff_thread
from .handoff_response import build_handoff_result, build_sdk_generate_result


async def dispatch_cursor_sdk_generate(
    *,
    request_id: str,
    role: str,
    messages: list[dict[str, Any]],
    model: str | None,
    subject: str | None,
    caller_agent: str | None,
    packet_path: str | None,
    message_text: str | None,
    reuse_thread: str | None = None,
    bus_lifecycle: Literal["persistent", "ephemeral"] | None = None,
) -> dict[str, Any]:
    """Execute cursor-sdk generate with to_thread default delivery.

    Consult: pass ``messages`` only → worker gets ``message=`` (last user turn).
    Implement: pass ``packet_path`` → worker gets ``packet_path=``.
    """
    to_agent, family, platform, resolved_model = resolve_cursor_sdk_generate_target(
        role, model=model, request_id=request_id
    )
    execution_id = str(uuid.uuid4())
    thread_subject = subject or f"cursor-sdk generate — {execution_id[:8]}"

    if packet_path is not None:
        pointer_body = f"SDK implement dispatch — see packet `{packet_path}`."
        worker_packet = packet_path
        worker_message = None
    else:
        last_user = message_text or extract_last_user_message(messages)
        if not last_user:
            from .admission import FrontierEndpointError

            raise FrontierEndpointError(
                request_id=request_id,
                field="messages",
                reason="At least one non-empty user message is required for consult",
                status_code=422,
            )
        pointer_body = last_user[:2000]
        worker_packet = None
        worker_message = last_user

    handoff_contract = "consult" if worker_packet is None else "implement"

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
            subject=thread_subject,
            pointer_body=pointer_body,
            caller_agent=caller_agent,
            tags=["cursor-sdk-generate"],
            handoff_contract=handoff_contract,
            lifecycle_state="pending",
            bus_lifecycle=bus_lifecycle,
        )
        emit_sdk_thread_created(
            request_id=request_id,
            to_agent=to_agent,
            thread_id=thread_id,
            reused=False,
        )

    await admit_handoff_dispatch(
        request_id=request_id,
        thread_id=thread_id,
        execution_id=execution_id,
        pipeline_id="cursor-sdk-generate",
        caller_agent=caller_agent,
    )

    if worker_packet is not None:
        worker_ok, worker_warning = await dispatch_cursor_sdk_worker(
            request_id=request_id,
            thread_id=thread_id,
            model=resolved_model,
            packet_path=worker_packet,
            handoff_contract=handoff_contract,
        )
    else:
        worker_ok, worker_warning = await dispatch_cursor_sdk_worker_message(
            request_id=request_id,
            thread_id=thread_id,
            model=resolved_model,
            message=worker_message or "",
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
        await post_worker_failure_turn(thread_id=thread_id, request_id=request_id)
        if worker_warning:
            warnings.append(worker_warning)

    profile = get_profile(family, platform)
    handoff_fields = build_handoff_result(thread_id=thread_id, to_agent=to_agent)
    return build_sdk_generate_result(
        role=role,
        profile=profile,
        handoff_fields=handoff_fields,
        execution_id=execution_id,
        thread_id=thread_id,
        to_agent=to_agent,
        resolved_model=resolved_model,
        warnings=warnings,
    )
