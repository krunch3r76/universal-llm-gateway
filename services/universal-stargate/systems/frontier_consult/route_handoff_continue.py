from __future__ import annotations

from functools import partial
from typing import Any

from agent_seat.profiles import get_profile
from implement_admission.skill_delivery_channels import SkillInlineBudgetExceeded

from .events import (
    FrontierHandoffCreated,
    FrontierHandoffExecutorOverride,
    FrontierHandoffMaterializationIncomplete,
    FrontierHandoffPacketEnriched,
    FrontierHandoffRequested,
)
from .executor_resolution import (
    _read_packet_executor_inputs,
    should_emit_executor_override_audit,
)
from .handoff import (
    _mcp_packet_seats,
    _resolve_packet_file,
    build_pointer_body,
    check_contract_ambiguity,
    create_handoff_thread,
    validate_packet,
)
from .handoff_life_mirror import (
    WEB_RECIPIENT_REACHABILITY,
    is_life_web_receiver,
    mirror_packet_file_to_cortex,
)
from .handoff_packet_enrich import (
    HandoffSkillInlineBudgetExceeded,
    HandoffSkillInlineMaterialized,
    enrich_handoff_packet,
)
from .handoff_response import (
    build_executor_recommendation_field,
    build_handoff_result,
    build_push_reminder,
    build_recommended_executor,
    build_recommended_review,
    build_seat_capability,
    resolve_poll_wait_seconds,
)
from .implement_admission_bridge import _executor_probe_root
from .implement_ready_gate import require_implement_ready
from .poll_hint_events import emit_poll_hint_from_handoff
from .service import FrontierEndpointError


async def continue_handoff_admission(
    *,
    request_id: str,
    body: Any,
    packet_path: str,
    contract_source: str,
    workspaces_root: Any,
    to_agent: str,
    family: str,
    platform: str,
    resolved_model: str,
    handoff_contract: str,
    reader: Any,
    loop: Any,
    publish: Any,
    warnings: list[str],
    materialization_present: bool | None,
) -> dict[str, Any]:
    check_contract_ambiguity(
        request_id=request_id,
        packet_path=packet_path,
        contract_source=contract_source,
        workspaces_root=workspaces_root,
    )

    enrich_result = None
    enrich_eligible = to_agent in _mcp_packet_seats() or is_life_web_receiver(to_agent)
    if enrich_eligible:
        packet_file = _resolve_packet_file(workspaces_root.resolve(), packet_path)
        if packet_file is not None:
            original = packet_file.read_text(encoding="utf-8", errors="replace")
            skill_delivery = (
                WEB_RECIPIENT_REACHABILITY.skill_delivery
                if is_life_web_receiver(to_agent)
                else None
            )
            try:
                enrich_result = enrich_handoff_packet(
                    original,
                    cortex=reader,
                    to_agent=to_agent,
                    skill_delivery=skill_delivery,
                    handoff_contract=handoff_contract,
                )
            except SkillInlineBudgetExceeded as exc:
                publish(
                    HandoffSkillInlineBudgetExceeded(
                        request_id=request_id,
                        packet_path=packet_path,
                        total_bytes=exc.total_bytes,
                        budget_bytes=exc.budget_bytes,
                        per_slug_bytes=exc.per_slug_bytes,
                    )
                )
                raise FrontierEndpointError(
                    request_id=request_id,
                    field="packet_path",
                    reason=exc.reason,
                    status_code=422,
                    code="skill_inline_budget_exceeded",
                    details={
                        "total_bytes": exc.total_bytes,
                        "budget_bytes": exc.budget_bytes,
                        "per_slug_bytes": exc.per_slug_bytes,
                    },
                ) from exc
            if enrich_result.changed:
                packet_file.write_text(enrich_result.text, encoding="utf-8")
                publish(
                    FrontierHandoffPacketEnriched(
                        request_id=request_id,
                        packet_path=packet_path,
                        to_agent=to_agent,
                        skills_added=enrich_result.skills_added,
                        skills_already_wired=enrich_result.skills_already_wired,
                        threads_added=enrich_result.threads_added,
                    )
                )
            if enrich_result.inline_materialized:
                publish(
                    HandoffSkillInlineMaterialized(
                        request_id=request_id,
                        packet_path=packet_path,
                        slugs=enrich_result.inline_slugs,
                        total_bytes=enrich_result.inline_total_bytes,
                    )
                )

    validation = validate_packet(
        request_id=request_id,
        packet_path=packet_path,
        to_agent=to_agent,
        handoff_contract=handoff_contract,
        workspaces_root=workspaces_root,
        source_ref=body.source_ref,
    )
    warnings.extend(validation.warnings)
    frontmatter_source_ref = validation.frontmatter_source_ref

    if handoff_contract == "implement":
        await loop.run_in_executor(
            None,
            partial(
                require_implement_ready,
                request_id=request_id,
                source_ref=body.source_ref or frontmatter_source_ref,
                cortex=reader,
            ),
        )

    publish(
        FrontierHandoffRequested(
            request_id=request_id,
            role=body.role or "",
            model=resolved_model,
            to_agent=to_agent,
            handoff_contract=handoff_contract,
        )
    )

    if materialization_present is False and body.source_ref is not None:
        publish(
            FrontierHandoffMaterializationIncomplete(
                request_id=request_id,
                packet_path=packet_path,
                probe_root=str(_executor_probe_root(workspaces_root.resolve())),
                source_ref=body.source_ref,
            )
        )

    pointer_packet_path = packet_path
    if is_life_web_receiver(to_agent):
        life_packet = _resolve_packet_file(workspaces_root.resolve(), packet_path)
        if life_packet is not None:
            pointer_packet_path = await loop.run_in_executor(
                None,
                partial(
                    mirror_packet_file_to_cortex,
                    life_packet,
                    packet_path=packet_path,
                ),
            )

    pointer = build_pointer_body(
        request_id=request_id,
        packet_path=pointer_packet_path,
        subject=body.subject,
        pointer_body=body.pointer_body,
        handoff_contract=handoff_contract,
        materialization_fallback=materialization_present is False,
        to_agent=to_agent,
        skill_inline_materialized=bool(
            enrich_result and enrich_result.inline_materialized
        ),
    )

    thread_id = await create_handoff_thread(
        request_id=request_id,
        to_agent=to_agent,
        subject=body.subject,
        pointer_body=pointer,
        caller_agent=body.caller_agent,
        tags=body.tags,
        handoff_contract=handoff_contract,
        bus_lifecycle=body.bus_lifecycle,
    )

    publish(
        FrontierHandoffCreated(
            request_id=request_id,
            to_agent=to_agent,
            thread_id=thread_id,
        )
    )

    packet_file = _resolve_packet_file(workspaces_root.resolve(), packet_path)
    packet_text = (
        packet_file.read_text(encoding="utf-8", errors="replace")
        if packet_file is not None
        else ""
    )
    executor_fields = build_recommended_executor(
        handoff_contract=handoff_contract,
        packet_text=packet_text,
        executor_override=body.executor_override,
        executor_override_reason_code=body.executor_override_reason_code,
        executor_override_reason=body.executor_override_reason,
    )
    review_fields = build_recommended_review(handoff_contract=handoff_contract)
    fm_override, _, _ = _read_packet_executor_inputs(packet_text)
    override_supplied = body.executor_override is not None or fm_override is not None
    if should_emit_executor_override_audit(
        handoff_contract=handoff_contract,
        recommended_executor=executor_fields.get("recommended_executor"),
        override_supplied=override_supplied,
    ):
        publish(
            FrontierHandoffExecutorOverride(
                request_id=request_id,
                handoff_contract=handoff_contract,
                recommended_executor=executor_fields["recommended_executor"]
                or "composer",
                source=executor_fields["recommended_executor_source"] or "",
                reason_code=executor_fields.get("recommended_executor_reason_code"),
            )
        )

    return {
        "thread_id": thread_id,
        "to_agent": to_agent,
        "family": family,
        "platform": platform,
        "resolved_model": resolved_model,
        "handoff_contract": handoff_contract,
        "contract_source": contract_source,
        "executor_fields": executor_fields,
        "review_fields": review_fields,
        "frontmatter_source_ref": frontmatter_source_ref,
    }


def assemble_handoff_result(
    *,
    request_id: str,
    body: Any,
    admitted: dict[str, Any],
    implement_spec_hash_value: str | None,
    caller_packet_path: str | None,
    materialization_present: bool | None,
    warnings: list[str],
) -> dict[str, Any]:
    thread_id = admitted["thread_id"]
    to_agent = admitted["to_agent"]
    family = admitted["family"]
    platform = admitted["platform"]
    resolved_model = admitted["resolved_model"]
    handoff_contract = admitted["handoff_contract"]
    contract_source = admitted["contract_source"]
    executor_fields = admitted["executor_fields"]
    review_fields = admitted["review_fields"]
    frontmatter_source_ref = admitted["frontmatter_source_ref"]

    handoff_fields = build_handoff_result(
        thread_id=thread_id,
        to_agent=to_agent,
        poll_wait_seconds=resolve_poll_wait_seconds(caller_agent=body.caller_agent),
    )
    emit_poll_hint_from_handoff(
        request_id=request_id,
        thread_id=thread_id,
        caller_agent=body.caller_agent,
        handoff_fields=handoff_fields,
    )
    result: dict[str, Any] = {
        "thread_id": thread_id,
        "subject": body.subject,
        "to_agent": to_agent,
        "resolved_model": resolved_model,
        "resolved_handoff_seat": to_agent,
        "handoff_contract": handoff_contract,
        "handoff_contract_source": contract_source,
        "push_reminder": build_push_reminder(
            thread_id=thread_id,
            to_agent=to_agent,
            platform=platform,
            handoff_contract=handoff_contract,
        ),
        **handoff_fields,
        **executor_fields,
        **build_executor_recommendation_field(
            handoff_contract=handoff_contract,
            target_surface=to_agent,
            target_model=resolved_model,
        ),
        **review_fields,
        **build_seat_capability(
            profile=get_profile(family, platform),
            recommended_executor=executor_fields.get("recommended_executor"),
        ),
    }
    if body.source_ref is not None:
        result["source_ref"] = body.source_ref
    if implement_spec_hash_value is not None:
        result["implement_spec_hash"] = implement_spec_hash_value
    if body.source_ref is not None and caller_packet_path is None:
        materialization_mode = "auto"
    elif caller_packet_path is not None and (
        body.source_ref is not None or frontmatter_source_ref is not None
    ):
        materialization_mode = "hand_authored_traced"
    else:
        materialization_mode = "hand_authored"
    result["materialization_mode"] = materialization_mode
    if materialization_present is False:
        result["materialization_present"] = False
    if warnings:
        result["warnings"] = warnings
    return result
