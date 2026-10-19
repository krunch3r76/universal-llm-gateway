"""Second half of cursor-sdk generate prepare."""

from __future__ import annotations

from typing import Any

from . import cursor_sdk_generate_prepare as _prepare
from .cursor_sdk_prepared_handle import PreparedCursorSdkHandle


async def complete_cursor_sdk_generate_prepare(
    bound: dict[str, Any],
) -> PreparedCursorSdkHandle:
    request_id = bound["request_id"]
    role = bound["role"]
    bound["model"]
    bound["subject"]
    caller_agent = bound["caller_agent"]
    contract = bound["contract"]
    handoff_contract = bound["handoff_contract"]
    bound["packet_path"]
    bound["message_text"]
    reuse_thread = bound["reuse_thread"]
    bound["bus_lifecycle"]
    parent_dispatch_thread_id = bound["parent_dispatch_thread_id"]
    dispatch_thread_id = bound["dispatch_thread_id"]
    bound["is_auto_consolidation"]
    density_triage = bound["density_triage"]
    review_opt_out_reason_code = bound["review_opt_out_reason_code"]
    bound["auto_review_child"]
    read_only = bound["read_only"]
    model_knobs = bound["model_knobs"]
    bound["cost_intent"]
    bound["suppress_cost_warning"]
    bound["cost_intent_reason"]
    bound["reasoning_effort"]
    bound["max_tool_turns"]
    execution_id = bound["execution_id"]
    dispatch_id = bound["dispatch_id"]
    source_ref = bound["source_ref"]
    bound["dispatch_lane"]
    nest_under = bound["nest_under"]
    lane = bound["lane"]
    workspace = bound["workspace"]
    refuse_if_lease_held = bound["refuse_if_lease_held"]
    prompt_turn_number = bound["prompt_turn_number"]
    prompt_bind_mode = bound["prompt_bind_mode"]
    bound["packet_kind"]
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
    bound["bus_body"]
    bound["off_bus_ref"]
    bound["prompt_sha256"]
    bound["requested_model_pin"]
    bound["admit_provenance"]
    bound["provenance_missing"]
    bound["effective_contract"]
    bound["raw_model"]
    bound["fence_pin"]
    alignment = bound["alignment"]
    aligned_knobs = bound["aligned_knobs"]
    to_agent = bound["to_agent"]
    thread_subject = bound["thread_subject"]
    thread_id = bound["thread_id"]
    pointer_turn = bound["pointer_turn"]
    claimed_via_atomic = bound["claimed_via_atomic"]
    pointer_body = bound["pointer_body"]
    worker_packet = bound["worker_packet"]
    worker_message = bound["worker_message"]
    effective_bus_lifecycle = bound["effective_bus_lifecycle"]
    early_packet_text = bound["early_packet_text"]
    effective_auto_review_child = bound["effective_auto_review_child"]
    auto_review_defaulted = bound["auto_review_defaulted"]
    bound["review_surface"]
    bound["resolved_dispatch_lane"]
    bound["suppress_review_spawn"]
    resolved_model = bound["resolved_model"]
    family = bound["family"]
    platform = bound["platform"]
    if claimed_via_atomic:
        admitted = True
    else:
        admitted = await _prepare.admit_handoff_dispatch(
            request_id=request_id,
            thread_id=thread_id,
            execution_id=execution_id,
            pipeline_id="cursor-sdk-generate",
            caller_agent=caller_agent,
            parent_thread_id=parent_dispatch_thread_id,
        )

    prompt_preamble: str | None = None
    packet_text: str | None = None
    if worker_packet is not None:
        preamble_pointer = pointer_body
        if handoff_contract == "implement" and "Contract:" not in pointer_body:
            preamble_pointer = f"Contract: implement.\n{pointer_body}"
        packet_text = early_packet_text or ""
        from .handoff import _resolve_packet_file, _workspaces_root

        packet_file = (
            _resolve_packet_file(_workspaces_root().resolve(), worker_packet)
            if not packet_text
            else None
        )
        if (
            contract != "conductor"
            and worker_packet is not None
            and not packet_text
            and packet_file is None
        ):
            from .admission import FrontierEndpointError

            probe_root = str(_workspaces_root().resolve())
            _prepare.emit_sdk_materialization_incomplete(
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
        if not packet_text and packet_file is not None:
            packet_text = packet_file.read_text(encoding="utf-8", errors="replace")
        if packet_text:
            from .diff_text_guard import assert_packet_free_of_diff_text

            assert_packet_free_of_diff_text(
                request_id=request_id,
                packet_path=worker_packet,
                text=packet_text,
            )
        prompt_preamble = _prepare.derive_cursor_sdk_prompt_preamble(
            handoff_contract=handoff_contract,
            pointer=preamble_pointer,
            packet_text=packet_text,
        )

    poll_after_turn = 1
    if reuse_thread is not None:
        if pointer_turn > 0:
            poll_after_turn = pointer_turn
        else:
            probed = await _prepare.probe_thread(thread_id)
            count = int(probed.get("turn_count") or 0) if probed else 0
            poll_after_turn = count if count > 0 else 1

    from implement_admission.dispatch_topic import (
        derive_handle_topic,
        extract_contract_from_body,
    )

    resolved_contract = (contract or "").strip().lower() or None
    if packet_text and not resolved_contract:
        resolved_contract = extract_contract_from_body(packet_text)
    handle_topic = derive_handle_topic(
        contract=resolved_contract,
        packet_text=packet_text,
        message_text=worker_message,
    )

    continuity_root_thread_id = await _prepare.probe_continuity_root_thread_id(
        parent_dispatch_thread_id
    )

    resolved_skills = _prepare.resolve_cursor_sdk_skills(
        skills,
        request_id=request_id,
        role=role,
        resolved_model=resolved_model,
    )

    return PreparedCursorSdkHandle(
        request_id=request_id,
        execution_id=execution_id,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        resolved_model=resolved_model,
        role=role,
        family=family,
        platform=platform,
        to_agent=to_agent,
        handoff_contract=handoff_contract,
        packet_path=worker_packet,
        message=worker_message,
        caller_agent=caller_agent,
        read_only=read_only,
        aligned_knobs=aligned_knobs,
        prompt_preamble=prompt_preamble,
        thread_subject=thread_subject,
        pointer_body=pointer_body,
        effective_bus_lifecycle=effective_bus_lifecycle,
        parent_dispatch_thread_id=parent_dispatch_thread_id,
        dispatch_thread_id=dispatch_thread_id,
        density_triage=density_triage,
        review_opt_out_reason_code=review_opt_out_reason_code,
        auto_review_child=effective_auto_review_child,
        auto_review_defaulted=auto_review_defaulted,
        claimed_via_atomic=claimed_via_atomic,
        admitted=admitted,
        alignment_warnings=tuple(alignment.warnings_as_dicts()),
        knob_resolution=tuple(alignment.knob_resolution_as_dicts()),
        nest_under=nest_under,
        topic=handle_topic,
        lane=lane,
        workspace=workspace,
        refuse_if_lease_held=refuse_if_lease_held,
        prompt_turn_number=prompt_turn_number,
        prompt_bind_mode=prompt_bind_mode,
        poll_after_turn=poll_after_turn,
        hop_from=hop_from,
        hop_seq=hop_seq,
        hop_reason=hop_reason,
        resume_of=resume_of,
        caller_dispatch_id=caller_dispatch_id,
        continuity_root_thread_id=continuity_root_thread_id,
        skills=resolved_skills,
        work_key=work_key,
        source_ref=source_ref,
        force=force,
        force_reason=force_reason,
        hop_park_release=hop_park_release,
        claim_id=claim_id,
        impresario_envelope=impresario_envelope,
        requested_model_knobs=dict(model_knobs or {}),
        wake_lane=wake_lane,
    )
