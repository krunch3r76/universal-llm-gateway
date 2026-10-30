"""Prepare stage for cursor-sdk generate — mint stable identities without worker POST.

Gate-6 condition: freeze the full fingerprint-bearing request
(thread_id, model, dispatch_id, execution_id, packet_path|message, read_only)
before the first worker admission attempt.
"""

from __future__ import annotations

from typing import Any, Literal

from universal_logging import get_logger

from .admit_provenance import (
    AdmitProvenanceContext,
)
from .cursor_sdk_alignment import align_cursor_knobs
from .cursor_sdk_coord_notify import post_coord_admit_pointer
from .cursor_sdk_generate_prepare_tail import finish_cursor_sdk_generate_prepare
from .cursor_sdk_generate_signals import (
    emit_sdk_generate_requested,
    emit_sdk_materialization_incomplete,
    emit_sdk_thread_created,
)
from .cursor_sdk_prepared_handle import (
    PreparedCursorSdkHandle,
    handle_from_dict,
    handle_to_dict,
    handle_with_thread,
    mint_cursor_sdk_ids,
)
from .cursor_sdk_reasoning_effort_reject import reject_nonempty_reasoning_effort
from .cursor_sdk_skills import resolve_cursor_sdk_skills
from .cursor_sdk_thread_reuse import (
    probe_continuity_root_thread_id,
    probe_thread,
    refuse_occupied_worker_thread,
)
from .cursor_sdk_worker_dispatch import derive_cursor_sdk_prompt_preamble
from .handoff import (
    PendingShellContention,
    admit_handoff_dispatch,
    build_generate_dispatch_pointer,
    build_pointer_only_dispatch_pointer,
    build_pointer_only_worker_message,
    claim_and_post_pointer_turn,
    create_handoff_thread,
    extract_generate_pointer_summary,
    post_pointer_turn,
)

logger = get_logger(__name__)


def _row_identity_tags(work_key: str | None, packet_path: str | None) -> list[str]:
    """Tags that keep the admitted row visible after the subject changes.

    A quiet-alarm replaces ``last_subject``. The packet name
    ``conductor-{slug}.md`` and a ``todo:`` work key are the row.
    """
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


_UNSET_MODEL_PIN: object = object()

__all__ = [
    "PendingShellContention",
    "PreparedCursorSdkHandle",
    "admit_handoff_dispatch",
    "claim_and_post_pointer_turn",
    "create_handoff_thread",
    "derive_cursor_sdk_prompt_preamble",
    "emit_sdk_generate_requested",
    "emit_sdk_materialization_incomplete",
    "emit_sdk_thread_created",
    "handle_from_dict",
    "handle_to_dict",
    "handle_with_thread",
    "mint_cursor_sdk_ids",
    "post_coord_admit_pointer",
    "post_pointer_turn",
    "prepare_cursor_sdk_generate",
    "probe_continuity_root_thread_id",
    "probe_thread",
    "refuse_occupied_worker_thread",
    "resolve_cursor_sdk_skills",
]


async def prepare_cursor_sdk_generate(
    *,
    request_id: str,
    role: str,
    model: str | None,
    subject: str | None,
    caller_agent: str | None,
    contract: Literal[
        "sketch",
        "implement",
        "wrap",
        "conductor",
        "pure-mechanical",
        "none",
    ],
    packet_path: str | None,
    message_text: str | None,
    reuse_thread: str | None = None,
    bus_lifecycle: Literal["persistent", "ephemeral"] | None = None,
    parent_dispatch_thread_id: str | None = None,
    dispatch_thread_id: str | None = None,
    is_auto_consolidation: bool = False,
    density_triage: str | None = None,
    review_opt_out_reason_code: str | None = None,
    auto_review_child: bool | None = None,
    read_only: bool = False,
    model_knobs: dict[str, str] | None = None,
    cost_intent: Literal["deliberate_high_cost"] | None = None,
    suppress_cost_warning: bool = False,
    cost_intent_reason: str | None = None,
    reasoning_effort: str | None = None,
    max_tool_turns: int | None = None,
    execution_id: str | None = None,
    dispatch_id: str | None = None,
    source_ref: str | None = None,
    dispatch_lane: str | None = None,
    nest_under: str | None = None,
    lane: Literal["A", "B"] | None = None,
    workspace: str | None = None,
    refuse_if_lease_held: bool = False,
    prompt_turn_number: int | None = None,
    prompt_bind_mode: str | None = None,
    packet_kind: str | None = None,
    hop_from: str | None = None,
    hop_seq: int | None = None,
    hop_reason: str | None = None,
    resume_of: str | None = None,
    caller_dispatch_id: str | None = None,
    skills: list[str] | None = None,
    work_key: str | None = None,
    force: bool = False,
    force_reason: str | None = None,
    hop_park_release: bool = False,
    claim_id: str | None = None,
    impresario_envelope: dict[str, Any] | None = None,
    wake_lane: str | None = None,
    bus_body: str | None = None,
    off_bus_ref: str | None = None,
    prompt_sha256: str | None = None,
    requested_model_pin: str | None | object = _UNSET_MODEL_PIN,
    admit_provenance: AdmitProvenanceContext | None = None,
) -> PreparedCursorSdkHandle:
    """Validate, mint/reuse IDs, create pending thread; do not POST the worker."""
    provenance_missing = (
        admit_provenance.missing if admit_provenance is not None else ()
    )
    from .generate_lane_ac_observer import (
        prepare_lb_auto_review_for_generate,
        stamp_lb_review_spawn_fields,
        validate_generate_contract_packet_rules,
    )

    effective_auto_review_child, auto_review_defaulted, early_packet_text = (
        prepare_lb_auto_review_for_generate(
            contract=contract,
            auto_review_child=auto_review_child,
            packet_path=packet_path,
            message_text=message_text,
        )
    )
    review_surface, resolved_dispatch_lane, suppress_review_spawn = (
        stamp_lb_review_spawn_fields(
            contract=contract,
            early_packet_text=early_packet_text,
            dispatch_lane=dispatch_lane,
            source_ref=source_ref,
            dispatch_thread_id=dispatch_thread_id,
            request_id=request_id,
        )
    )

    from .densify_triage import validate_generate_density_intake

    validate_generate_density_intake(
        request_id=request_id,
        contract=contract,
        density_triage=density_triage,
        review_opt_out_reason_code=review_opt_out_reason_code,
        auto_review_child=effective_auto_review_child,
    )
    from implement_admission.dispatch_topic import extract_contract_from_body

    from .admission import enforce_check_review_substrate_admission

    effective_contract = (contract or "").strip().lower() or None
    if early_packet_text and not effective_contract:
        effective_contract = extract_contract_from_body(early_packet_text)
    from cursor_capabilities import is_cursor_model_denied
    from implement_admission.routing import load_route_policy
    from implement_admission.workflow_registry import load_auto_judgment_default_model

    from .admission import FrontierEndpointError

    raw_model = str(model).strip() if model else ""
    if raw_model:
        resolved_model = raw_model
    else:
        resolved_model = load_auto_judgment_default_model(load_route_policy())
    if resolved_model == "auto":
        resolved_model = "cursor/auto"
    if not resolved_model:
        raise FrontierEndpointError(
            request_id=request_id,
            field="model",
            reason="cursor-sdk generate requires a resolvable model",
            status_code=422,
            code="sdk_generate_model_invalid",
        )
    if not resolved_model.startswith("cursor/"):
        raise FrontierEndpointError(
            request_id=request_id,
            field="model",
            reason="cursor-sdk generate requires a cursor/* model",
            status_code=422,
            code="sdk_generate_model_invalid",
        )
    enforce_check_review_substrate_admission(
        role, resolved_model, request_id=request_id, job=effective_contract
    )
    if is_cursor_model_denied(resolved_model):
        raise FrontierEndpointError(
            request_id=request_id,
            field="model",
            reason="cursor model is denied for sdk generate",
            status_code=422,
            code="sdk_generate_model_invalid",
        )
    from .cursor_sdk_model_pin import refuse_code_contract_model_mismatch

    pin_text = requested_model_pin if isinstance(requested_model_pin, str) else None
    refuse_code_contract_model_mismatch(
        request_id=request_id,
        contract=effective_contract,
        requested_model=pin_text,
        resolved_model=resolved_model,
    )
    to_agent, family, platform = "cursor-sdk", "cursor", "sdk"
    from .cursor_sdk_pool_fence import reject_other_models_pool_generate

    fence_pin = (
        model if requested_model_pin is _UNSET_MODEL_PIN else requested_model_pin
    )
    reject_other_models_pool_generate(
        request_id=request_id,
        role=role,
        seat=None,
        model=fence_pin if isinstance(fence_pin, str) else None,
        resolved_model=resolved_model,
    )
    from .cursor_sdk_generate_prepare_restart_busy import (
        enforce_restart_busy_scoped_by_work_key,
    )

    await enforce_restart_busy_scoped_by_work_key(
        request_id=request_id,
        work_key=work_key,
        admit_provenance=admit_provenance,
        nest_under=nest_under,
        caller_dispatch_id=caller_dispatch_id,
        resume_of=resume_of,
    )
    if execution_id is None or dispatch_id is None:
        minted_execution, minted_dispatch = mint_cursor_sdk_ids(request_id=request_id)
        execution_id = execution_id or minted_execution
        dispatch_id = dispatch_id or minted_dispatch

    reject_nonempty_reasoning_effort(
        request_id=request_id,
        resolved_model=resolved_model,
        reasoning_effort=reasoning_effort,
    )

    alignment = align_cursor_knobs(
        resolved_model=resolved_model,
        contract=contract,
        model_knobs=model_knobs,
        cost_intent=cost_intent,
        suppress_cost_warning=suppress_cost_warning,
        cost_intent_reason=cost_intent_reason,
        max_tool_turns=max_tool_turns,
        request_id=request_id,
        execution_id=execution_id,
    )
    aligned_knobs = alignment.aligned_knobs or None
    to_agent = f"cursor-sdk:dispatch:{execution_id}"
    thread_subject = subject or f"cursor-sdk generate — {execution_id[:8]}"

    validate_generate_contract_packet_rules(
        request_id=request_id,
        contract=contract,
        packet_path=packet_path,
        read_only=read_only,
    )

    if contract == "conductor" and packet_path is None and message_text:
        nbytes = len(message_text)
        pointer_body = f"SDK {contract} dispatch — inline packet ({nbytes} bytes)."
        worker_packet = None
        worker_message = message_text
    elif packet_path is not None:
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
        pointer_body = build_generate_dispatch_pointer(
            lane="SDK",
            contract=contract,
            dispatch_thread_id=dispatch_thread_id,
            correlation_id=execution_id,
            summary=extract_generate_pointer_summary(last_user),
            prompt_turn_number=prompt_turn_number,
        )
        worker_packet = None
        worker_message = last_user

    if bus_body == "pointer_only":
        pointer_body = build_pointer_only_dispatch_pointer(
            lane="SDK",
            contract=contract,
            sidecar_ref=off_bus_ref,
            prompt_sha256=prompt_sha256,
            correlation_id=execution_id,
        )
        worker_message = build_pointer_only_worker_message(
            sidecar_ref=off_bus_ref,
            prompt_sha256=prompt_sha256,
        )
        worker_packet = None

    return await finish_cursor_sdk_generate_prepare(locals())
