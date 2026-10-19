"""Tail of cursor-sdk generate prepare, split for the 350-line cap."""

from __future__ import annotations

from typing import Any, Literal

from universal_logging import get_logger

from .admit_provenance import emit_admit_provenance_missing_event
from .cursor_sdk_coord_notify import post_coord_admit_pointer
from .cursor_sdk_generate_prepare_close import complete_cursor_sdk_generate_prepare
from .cursor_sdk_generate_signals import (
    emit_sdk_generate_requested,
    emit_sdk_thread_created,
)
from .cursor_sdk_prepared_handle import PreparedCursorSdkHandle
from .cursor_sdk_thread_reuse import (
    refuse_occupied_worker_thread,
)
from .handoff import (
    PendingShellContention,
    claim_and_post_pointer_turn,
    create_handoff_thread,
    post_pointer_turn,
)

logger = get_logger(__name__)


def _row_identity_tags(work_key: str | None, packet_path: str | None) -> list[str]:
    tags: list[str] = []
    key = str(work_key or "").strip().lower()
    if key.startswith("todo:") and len(key) > 5:
        tags.append(key)
    name = str(packet_path or "").replace("\\", "/").rsplit("/", 1)[-1].lower()
    if name.startswith("conductor-") and name.endswith(".md"):
        slug = name[len("conductor-") : -len(".md")]
        if slug and all(c.isalnum() or c == "-" for c in slug):
            tag = f"todo:{slug}"
            if tag not in tags:
                tags.append(tag)
    return tags


async def finish_cursor_sdk_generate_prepare(
    bound: dict[str, Any],
) -> PreparedCursorSdkHandle:
    request_id = bound["request_id"]
    role = bound["role"]
    model = bound["model"]
    subject = bound["subject"]
    caller_agent = bound["caller_agent"]
    contract = bound["contract"]
    packet_path = bound["packet_path"]
    message_text = bound["message_text"]
    reuse_thread = bound["reuse_thread"]
    bus_lifecycle = bound["bus_lifecycle"]
    parent_dispatch_thread_id = bound["parent_dispatch_thread_id"]
    dispatch_thread_id = bound["dispatch_thread_id"]
    is_auto_consolidation = bound["is_auto_consolidation"]
    density_triage = bound["density_triage"]
    review_opt_out_reason_code = bound["review_opt_out_reason_code"]
    auto_review_child = bound["auto_review_child"]
    read_only = bound["read_only"]
    model_knobs = bound["model_knobs"]
    cost_intent = bound["cost_intent"]
    suppress_cost_warning = bound["suppress_cost_warning"]
    cost_intent_reason = bound["cost_intent_reason"]
    reasoning_effort = bound["reasoning_effort"]
    max_tool_turns = bound["max_tool_turns"]
    execution_id = bound["execution_id"]
    dispatch_id = bound["dispatch_id"]
    source_ref = bound["source_ref"]
    dispatch_lane = bound["dispatch_lane"]
    nest_under = bound["nest_under"]
    lane = bound["lane"]
    workspace = bound["workspace"]
    refuse_if_lease_held = bound["refuse_if_lease_held"]
    prompt_turn_number = bound["prompt_turn_number"]
    prompt_bind_mode = bound["prompt_bind_mode"]
    packet_kind = bound["packet_kind"]
    hop_from = bound["hop_from"]
    hop_seq = bound["hop_seq"]
    hop_reason = bound["hop_reason"]
    resume_of = bound["resume_of"]
    caller_dispatch_id = bound["caller_dispatch_id"]
    skills = bound["skills"]
    work_key = bound["work_key"]
    force = bound["force"]
    force_reason = bound["force_reason"]
    hop_park_release = bound["hop_park_release"]
    claim_id = bound["claim_id"]
    impresario_envelope = bound["impresario_envelope"]
    wake_lane = bound["wake_lane"]
    bus_body = bound["bus_body"]
    off_bus_ref = bound["off_bus_ref"]
    prompt_sha256 = bound["prompt_sha256"]
    requested_model_pin = bound["requested_model_pin"]
    admit_provenance = bound["admit_provenance"]
    provenance_missing = bound["provenance_missing"]
    effective_contract = bound["effective_contract"]
    raw_model = bound["raw_model"]
    fence_pin = bound["fence_pin"]
    alignment = bound["alignment"]
    aligned_knobs = bound["aligned_knobs"]
    to_agent = bound["to_agent"]
    thread_subject = bound["thread_subject"]
    resolved_model = bound.get("resolved_model")
    pointer_body = bound.get("pointer_body")
    worker_packet = bound.get("worker_packet")
    worker_message = bound.get("worker_message")
    early_packet_text = bound.get("early_packet_text")
    effective_auto_review_child = bound.get("effective_auto_review_child")
    auto_review_defaulted = bound.get("auto_review_defaulted")
    review_surface = bound.get("review_surface")
    resolved_dispatch_lane = bound.get("resolved_dispatch_lane")
    suppress_review_spawn = bound.get("suppress_review_spawn")
    handoff_contract = contract
    effective_bus_lifecycle: Literal["persistent", "ephemeral"] = (
        bus_lifecycle if bus_lifecycle is not None else "ephemeral"
    )
    coord_recipient = caller_agent or "dispatch"

    # Wrap skips prompt resolution for packets; classify/coord share this mode.
    if packet_path is not None and not prompt_bind_mode:
        prompt_bind_mode = "explicit_external"
    has_explicit_prompt_source = packet_path is not None or prompt_bind_mode in (
        "explicit_inline",
        "explicit_external",
    )
    admit_target = (parent_dispatch_thread_id or "").strip()
    prompt_source = (dispatch_thread_id or "").strip()
    if admit_target and prompt_source:
        from .cursor_sdk_admit_loop import (
            classify_admit_pointer_loop,
            loop_closure_refuse_error,
        )
        from .cursor_sdk_coord_notify import emit_loop_closure_admission

        classification = classify_admit_pointer_loop(
            admit_target_thread=admit_target,
            prompt_source_thread=prompt_source,
            prompt_bind_mode=prompt_bind_mode,
            prompt_turn_number=prompt_turn_number,
            has_explicit_prompt_source=has_explicit_prompt_source,
        )
        if classification.loop_closure:
            emit_loop_closure_admission(
                request_id=request_id,
                execution_id=execution_id,
                admit_target_thread=admit_target,
                prompt_source_thread=prompt_source,
                prompt_bind_mode=prompt_bind_mode,
                prompt_turn_number=prompt_turn_number,
                has_explicit_prompt_source=has_explicit_prompt_source,
                refused=True,
            )
            raise loop_closure_refuse_error(
                request_id=request_id,
                classification=classification,
                admit_target_thread=admit_target,
                prompt_source_thread=prompt_source,
                prompt_bind_mode=prompt_bind_mode,
                prompt_turn_number=prompt_turn_number,
            )

    emit_sdk_generate_requested(
        request_id=request_id,
        role=role,
        execution_id=execution_id,
        handoff_contract=handoff_contract,
        resolved_model=resolved_model,
    )

    claimed_via_atomic = False
    thread_id: str
    pointer_turn = 0

    if reuse_thread is not None:
        await refuse_occupied_worker_thread(
            request_id=request_id,
            reuse_thread=reuse_thread,
            nest_under=nest_under,
            read_only=read_only,
        )
        thread_id = reuse_thread
        if is_auto_consolidation:
            try:
                pointer_turn = await claim_and_post_pointer_turn(
                    request_id=request_id,
                    thread_id=thread_id,
                    to_agent=to_agent,
                    subject=thread_subject,
                    pointer_body=pointer_body,
                    caller_agent=caller_agent,
                    execution_id=execution_id,
                    pipeline_id="cursor-sdk-generate",
                    parent_thread_id=parent_dispatch_thread_id,
                    provenance_missing=provenance_missing,
                )
                claimed_via_atomic = True
                emit_sdk_thread_created(
                    request_id=request_id,
                    to_agent=to_agent,
                    thread_id=thread_id,
                    reused=True,
                )
            except PendingShellContention:
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
                        *_row_identity_tags(work_key, packet_path),
                    ],
                    handoff_contract=handoff_contract,
                    lifecycle_state="pending",
                    bus_lifecycle=effective_bus_lifecycle,
                    provenance_missing=provenance_missing,
                )
                emit_sdk_thread_created(
                    request_id=request_id,
                    to_agent=to_agent,
                    thread_id=thread_id,
                    reused=False,
                )
        else:
            pointer_turn = await post_pointer_turn(
                request_id=request_id,
                thread_id=thread_id,
                to_agent=to_agent,
                subject=thread_subject,
                pointer_body=pointer_body,
                caller_agent=caller_agent,
                provenance_missing=provenance_missing,
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
                *_row_identity_tags(work_key, packet_path),
            ],
            handoff_contract=handoff_contract,
            lifecycle_state="pending",
            bus_lifecycle=effective_bus_lifecycle,
            provenance_missing=provenance_missing,
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
        request_id=request_id,
        execution_id=execution_id,
        prompt_source_thread=dispatch_thread_id,
        prompt_bind_mode=prompt_bind_mode,
        prompt_turn_number=prompt_turn_number,
        has_explicit_prompt_source=has_explicit_prompt_source,
        admit_provenance=admit_provenance,
    )

    from implement_admission.check_review_substrate import consultant_rung

    from .generate_admission_context_store import write_admission_context

    try:
        write_admission_context(
            execution_id=execution_id,
            auto_review_child=effective_auto_review_child,
            op="generate",
            role=role,
            resolved_model=resolved_model,
            parent_dispatch_thread_id=parent_dispatch_thread_id,
            dispatch_thread_id=dispatch_thread_id,
            review_surface=review_surface,
            dispatch_lane=resolved_dispatch_lane,
            suppress_review_spawn=suppress_review_spawn,
            prompt_turn_number=prompt_turn_number,
            prompt_bind_mode=prompt_bind_mode,
            resolved_effort=consultant_rung(resolved_model, aligned_knobs),
            caller_seat=(
                admit_provenance.caller_seat
                if admit_provenance is not None
                else (
                    caller_agent.strip()
                    if caller_agent and caller_agent.strip()
                    else None
                )
            ),
            work_key=(
                admit_provenance.work_key
                if admit_provenance is not None
                else (work_key.strip() if work_key and work_key.strip() else None)
            ),
        )
    except Exception:
        logger.warning(
            "admission context write failed; admit continues: execution_id=%s",
            execution_id,
            exc_info=True,
        )
        if admit_provenance is not None:
            admit_provenance.note_provenance_record_failed()
            emit_admit_provenance_missing_event(
                admit_provenance,
                execution_id=execution_id,
                provenance_record_failed=True,
            )

    return await complete_cursor_sdk_generate_prepare(locals())
