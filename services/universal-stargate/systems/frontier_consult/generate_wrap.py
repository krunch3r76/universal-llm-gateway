"""Gate-then-materialize for the op=generate cursor-sdk implement lane."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi.responses import JSONResponse
from implement_admission.admission_read import frontmatter_value
from implement_admission.preflight import (
    DecisionNotAssertedError,
    RouteContractContradictionError,
    require_decision_asserted,
)
from implement_admission.source_ref import SourceRefError

from .cursor_sdk_generate import dispatch_cursor_sdk_generate
from .cursor_sdk_thread_reuse import (
    consolidation_split_warning,
    probe_thread,
    resolve_cursor_sdk_thread_targets,
)
from .dispatch_thread_context import resolve_generate_prompt_resolution
from .handoff import _resolve_packet_file, _workspaces_root
from .implement_admission_bridge import resolve_source_ref_to_packet
from .implement_ready_gate import require_implement_ready
from .stargate_cortex_reader import StargateCortexReader

if TYPE_CHECKING:
    from fastapi import Response

    from .route import TeamDispatchGenerateBody


@dataclass(frozen=True, slots=True)
class GenerateWrapResult:
    packet_path: str | None
    gated: bool = False
    gated_reason: str | None = None
    materialized: bool = False
    warnings: list[str] = field(default_factory=list)
    implement_spec_hash: str | None = None
    packet_sha256: str | None = None
    materialization_present: bool | None = None
    route_contract: dict[str, Any] | None = None
    scoreboard_uri: str | None = None


def prepare_conductor_packet(
    *,
    request_id: str,
    source_ref: str,
    caller_agent: str | None,
    summon_text: str | None = None,
    summon_mode: str | None = None,
    cortex: StargateCortexReader,
    workspaces_root: Path,
    role: str = "cursor-sdk",
    transport: str = "team_dispatch",
    summoning_thread_id: str | None = None,
    summoning_turn_count: int | None = None,
) -> GenerateWrapResult:
    """Materialize a conductor six-block packet from ``source_ref=todo:``."""
    del request_id, role, transport
    bridge = resolve_source_ref_to_packet(
        source_ref,
        cortex=cortex,
        workspaces_root=workspaces_root,
        packet_kind="conductor",
        contract="light-bounded",
        caller_agent=caller_agent,
        summon_text=summon_text,
        summon_mode=summon_mode,
        summoning_thread_id=summoning_thread_id,
        summoning_turn_count=summoning_turn_count,
    )
    if bridge.gated:
        return GenerateWrapResult(
            packet_path=None,
            gated=True,
            gated_reason=bridge.gated_reason,
        )
    from implement_admission.conductor_materialize import extract_scoreboard_uri

    scoreboard_uri: str | None = None
    materialized_path = bridge.packet_path
    if materialized_path is not None:
        materialized_file = _resolve_packet_file(
            workspaces_root.resolve(), materialized_path
        )
        if materialized_file is not None:
            scoreboard_uri = extract_scoreboard_uri(
                materialized_file.read_text(encoding="utf-8", errors="replace")
            )
    return GenerateWrapResult(
        packet_path=materialized_path,
        materialized=True,
        warnings=list(bridge.warnings),
        packet_sha256=bridge.packet_sha256,
        materialization_present=bridge.materialization_present,
        route_contract=bridge.route_contract,
        scoreboard_uri=scoreboard_uri,
    )


def prepare_implement_packet(
    *,
    request_id: str,
    source_ref: str | None,
    packet_path: str | None,
    caller_agent: str | None,
    cortex: StargateCortexReader,
    workspaces_root: Path,
    contract: str = "implement",
    role: str = "cursor-sdk",
    operator_pickup_required: bool | None = None,
    autonomy: str | None = None,
    transport: str = "team_dispatch",
) -> GenerateWrapResult:
    """Hard-gate, then materialize the implement packet when none was supplied.

    Order is gate-then-materialize (Fork A): require_implement_ready runs first
    on the effective source_ref so a non-dense / unasserted spec is rejected
    BEFORE any materialization side effect. The materialization sub-path
    (no caller packet) additionally runs require_decision_asserted — parity with
    the handoff lane / deprecated seat=cursor-sdk alias it replaces (Fork E).
    Inline packet_path is the fallback and is returned unchanged.
    """
    gate_source_ref = source_ref
    if packet_path:
        packet_file = _resolve_packet_file(workspaces_root.resolve(), packet_path)
        if packet_file is not None:
            gate_source_ref = frontmatter_value(
                packet_file.read_text(encoding="utf-8", errors="replace"),
                "source_ref",
            )

    # HARD GATE (unchanged): triage + implement-ready assertion + dense-spec
    # content + spec_sha256. No-op for non-todo source kinds.
    require_implement_ready(
        request_id=request_id,
        source_ref=gate_source_ref,
        cortex=cortex,
    )

    if packet_path is not None:
        packet_file = _resolve_packet_file(workspaces_root.resolve(), packet_path)
        if packet_file is not None:
            from .diff_text_guard import assert_packet_free_of_diff_text

            assert_packet_free_of_diff_text(
                request_id=request_id,
                packet_path=packet_path,
                text=packet_file.read_text(encoding="utf-8", errors="replace"),
            )
        return GenerateWrapResult(packet_path=packet_path)

    if source_ref is None:
        return GenerateWrapResult(packet_path=None)

    # MASTER RATIFICATION (materialization sub-path only): the unified-admission
    # decision must be ratified before invoking the materialization machinery —
    # parity with the handoff lane + deprecated seat=cursor-sdk alias (GPT-5.5 F1).
    require_decision_asserted(cortex=cortex)

    # FIRST-CLASS WRAP: materialize server-side (reuse the handoff-lane bridge).
    bridge = resolve_source_ref_to_packet(
        source_ref,
        cortex=cortex,
        workspaces_root=workspaces_root,
        request_id=request_id,
        author_family=caller_agent,
        contract=contract,
        role=role,
        transport=transport,
        operator_pickup_required=operator_pickup_required,
        autonomy=autonomy,
    )
    if bridge.gated:
        return GenerateWrapResult(
            packet_path=None,
            gated=True,
            gated_reason=bridge.gated_reason,
        )
    materialized_path = bridge.packet_path
    if materialized_path is not None:
        materialized_file = _resolve_packet_file(
            workspaces_root.resolve(), materialized_path
        )
        if materialized_file is not None:
            from .diff_text_guard import assert_packet_free_of_diff_text

            assert_packet_free_of_diff_text(
                request_id=request_id,
                packet_path=materialized_path,
                text=materialized_file.read_text(encoding="utf-8", errors="replace"),
            )
    return GenerateWrapResult(
        packet_path=materialized_path,
        materialized=True,
        warnings=list(bridge.warnings),
        implement_spec_hash=bridge.implement_spec_hash,
        packet_sha256=bridge.packet_sha256,
        materialization_present=bridge.materialization_present,
        route_contract=bridge.route_contract,
    )


async def dispatch_cursor_sdk_generate_route(
    *,
    request_id: str,
    body: TeamDispatchGenerateBody,
    seat: str,
    response: Response,
) -> dict[str, Any] | JSONResponse:
    """Cursor-sdk generate branch: gate-then-materialize + SDK orchestrator."""
    from .admission import FrontierEndpointError
    from .cursor_sdk_lane_gate import (
        reject_resume_of_conflicts,
        require_cursor_sdk_checkout_lane,
    )

    try:
        require_cursor_sdk_checkout_lane(
            request_id=request_id,
            lane=getattr(body, "lane", None),
            nest_under=getattr(body, "nest_under", None),
            resume_of=getattr(body, "resume_of", None),
            contract=body.contract,
        )
        reject_resume_of_conflicts(
            request_id=request_id,
            resume_of=getattr(body, "resume_of", None),
            nest_under=getattr(body, "nest_under", None),
            reuse_thread=getattr(body, "reuse_thread", None),
        )
    except FrontierEndpointError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    role = seat
    try:
        source_ref = getattr(body, "source_ref", None)
        if body.contract == "wrap":
            if getattr(body, "packet_path", None) is not None:
                return JSONResponse(
                    status_code=422,
                    content=FrontierEndpointError(
                        request_id=request_id,
                        field="packet_path",
                        reason="packet_path is forbidden for contract=wrap",
                        status_code=422,
                        code="wrap_with_packet_path",
                    ).to_dict(),
                )
            if source_ref is None:
                return JSONResponse(
                    status_code=422,
                    content=FrontierEndpointError(
                        request_id=request_id,
                        field="source_ref",
                        reason="source_ref is required for contract=wrap",
                        status_code=422,
                        code="wrap_requires_source_ref",
                    ).to_dict(),
                )
            loop = asyncio.get_running_loop()
            wrap_result = await loop.run_in_executor(
                None,
                partial(
                    prepare_implement_packet,
                    request_id=request_id,
                    source_ref=source_ref,
                    packet_path=None,
                    caller_agent=body.caller_agent,
                    cortex=StargateCortexReader(),
                    workspaces_root=_workspaces_root(),
                    contract="wrap",
                    role=role,
                ),
            )
            if wrap_result.gated:
                return JSONResponse(
                    status_code=422,
                    content=FrontierEndpointError(
                        request_id=request_id,
                        field="source_ref",
                        reason=(
                            wrap_result.gated_reason or "source_ref not implement-ready"
                        ),
                        status_code=422,
                        code="generate_source_ref_gated",
                    ).to_dict(),
                )
            response.status_code = 200
            payload = {
                "contract": "wrap",
                "status": "materialized",
                "materialized": True,
                "materialization_mode": "auto",
                "source_ref": source_ref,
                "packet_path": wrap_result.packet_path,
                "implement_spec_hash": wrap_result.implement_spec_hash,
                "packet_sha256": wrap_result.packet_sha256,
                "materialization_present": wrap_result.materialization_present,
                "warnings": list(wrap_result.warnings),
                "request_id": request_id,
            }
            if wrap_result.route_contract is not None:
                payload["route_contract"] = wrap_result.route_contract
            return payload

        wrap = GenerateWrapResult(packet_path=getattr(body, "packet_path", None))
        packet_kind = getattr(body, "packet_kind", None)
        source_ref = getattr(body, "source_ref", None)
        if (
            body.contract == "light-bounded"
            and (packet_kind or "").lower() == "conductor"
            and source_ref
            and not getattr(body, "packet_path", None)
        ):
            loop = asyncio.get_running_loop()
            gen_opts = getattr(body, "generation_options", None) or {}
            raw_summon = gen_opts.get("summon_mode")
            summoning_turn_count: int | None = None
            dispatch_tid = getattr(body, "dispatch_thread_id", None)
            if dispatch_tid and str(dispatch_tid).strip():
                thread_payload = await probe_thread(str(dispatch_tid).strip())
                if thread_payload is not None:
                    summoning_turn_count = int(thread_payload.get("turn_count") or 0)
            wrap = await loop.run_in_executor(
                None,
                partial(
                    prepare_conductor_packet,
                    request_id=request_id,
                    source_ref=source_ref,
                    caller_agent=body.caller_agent,
                    summon_text=getattr(body, "prompt", None),
                    summon_mode=(
                        str(raw_summon).strip() if raw_summon else None
                    ),
                    cortex=StargateCortexReader(),
                    workspaces_root=_workspaces_root(),
                    summoning_thread_id=(
                        str(body.dispatch_thread_id).strip()
                        if getattr(body, "dispatch_thread_id", None)
                        else None
                    ),
                    summoning_turn_count=summoning_turn_count,
                ),
            )
        elif body.contract == "implement":
            loop = asyncio.get_running_loop()
            wrap = await loop.run_in_executor(
                None,
                partial(
                    prepare_implement_packet,
                    request_id=request_id,
                    source_ref=getattr(body, "source_ref", None),
                    packet_path=getattr(body, "packet_path", None),
                    caller_agent=body.caller_agent,
                    cortex=StargateCortexReader(),
                    workspaces_root=_workspaces_root(),
                    contract=body.contract,
                    role=role,
                ),
            )
            if wrap.gated:
                return JSONResponse(
                    status_code=422,
                    content=FrontierEndpointError(
                        request_id=request_id,
                        field="source_ref",
                        reason=wrap.gated_reason or "source_ref not implement-ready",
                        status_code=422,
                        code="generate_source_ref_gated",
                    ).to_dict(),
                )
        elif getattr(body, "packet_path", None) is not None:
            packet_path = body.packet_path
            packet_file = _resolve_packet_file(
                _workspaces_root().resolve(), packet_path
            )
            if packet_file is None:
                return JSONResponse(
                    status_code=422,
                    content=FrontierEndpointError(
                        request_id=request_id,
                        field="packet_path",
                        reason=(
                            f"packet_path {packet_path!r} not found under "
                            "workspaces root"
                        ),
                        status_code=422,
                        code="packet_path_unresolved",
                    ).to_dict(),
                )
            wrap = GenerateWrapResult(packet_path=packet_path)
        has_packet = wrap.packet_path is not None
        prompt_resolution = None
        if body.contract == "implement" or has_packet:
            source_text = ""
        else:
            prompt_resolution = await resolve_generate_prompt_resolution(
                request_id=request_id,
                role=role,
                dispatch_thread_id=body.dispatch_thread_id,
                prompt=getattr(body, "prompt", None),
                sidecar_ref=getattr(body, "sidecar_ref", None),
            )
            source_text = prompt_resolution.text
        (
            reuse_thread,
            parent_dispatch_thread_id,
            is_auto_consolidation,
        ) = await resolve_cursor_sdk_thread_targets(
            reuse_thread=getattr(body, "reuse_thread", None),
            dispatch_thread_id=body.dispatch_thread_id,
            packet_kind=getattr(body, "packet_kind", None),
            request_id=request_id,
        )
        result = await dispatch_cursor_sdk_generate(
            request_id=request_id,
            role=role,
            model=getattr(body, "model", None),
            subject=None,
            caller_agent=body.caller_agent,
            contract=body.contract,
            packet_path=wrap.packet_path,
            message_text=source_text,
            reuse_thread=reuse_thread,
            bus_lifecycle=getattr(body, "bus_lifecycle", None),
            parent_dispatch_thread_id=parent_dispatch_thread_id,
            dispatch_thread_id=body.dispatch_thread_id,
            is_auto_consolidation=is_auto_consolidation,
            density_triage=getattr(body, "density_triage", None),
            review_opt_out_reason_code=getattr(
                body, "review_opt_out_reason_code", None
            ),
            auto_review_child=getattr(body, "auto_review_child", False),
            model_knobs=getattr(body, "model_knobs", None),
            cost_intent=getattr(body, "cost_intent", None),
            suppress_cost_warning=getattr(body, "suppress_cost_warning", False),
            cost_intent_reason=getattr(body, "cost_intent_reason", None),
            reasoning_effort=getattr(body, "reasoning_effort", None),
            max_tool_turns=getattr(body, "max_tool_turns", None),
            prompt_turn_number=(
                getattr(body, "prompt_turn_number", None)
                if getattr(body, "prompt_turn_number", None) is not None
                else (
                    prompt_resolution.prompt_turn_number
                    if prompt_resolution
                    else None
                )
            ),
            prompt_bind_mode=(
                getattr(body, "prompt_bind_mode", None)
                or (
                    prompt_resolution.prompt_bind_mode
                    if prompt_resolution
                    else None
                )
            ),
            source_ref=getattr(body, "source_ref", None),
            dispatch_lane=getattr(body, "dispatch_lane", None),
            nest_under=getattr(body, "nest_under", None),
            lane=getattr(body, "lane", None),
            workspace=getattr(body, "workspace", None),
            read_only=getattr(body, "read_only", False),
            refuse_if_lease_held=getattr(body, "refuse_if_lease_held", False),
            packet_kind=getattr(body, "packet_kind", None),
            hop_from=getattr(body, "hop_from", None),
            hop_seq=getattr(body, "hop_seq", None),
            hop_reason=getattr(body, "hop_reason", None),
            resume_of=getattr(body, "resume_of", None),
            skills=getattr(body, "skills", None),
        )
        if isinstance(result, dict):
            split_warning = consolidation_split_warning(
                reuse_thread=reuse_thread,
                parent_dispatch_thread_id=parent_dispatch_thread_id,
            )
            if split_warning:
                result["warnings"] = list(result.get("warnings") or []) + [
                    split_warning
                ]
        if isinstance(result, dict) and wrap.materialized:
            result["materialization_mode"] = "auto"
            if wrap.warnings:
                result["warnings"] = list(result.get("warnings") or []) + wrap.warnings
            if wrap.route_contract is not None:
                result["route_contract"] = wrap.route_contract
            if wrap.scoreboard_uri is not None:
                result["scoreboard_uri"] = wrap.scoreboard_uri
    except RouteContractContradictionError as exc:
        return JSONResponse(
            status_code=422,
            content=FrontierEndpointError(
                request_id=request_id,
                field=exc.field,
                reason=str(exc),
                status_code=422,
                code=exc.code,
            ).to_dict(),
        )
    except SourceRefError as exc:
        return JSONResponse(
            status_code=422,
            content=FrontierEndpointError(
                request_id=request_id,
                field="source_ref",
                reason=f"{exc.rule} ({exc.source_ref})",
                status_code=422,
                code=exc.code,
            ).to_dict(),
        )
    except DecisionNotAssertedError as exc:
        return JSONResponse(
            status_code=422,
            content=FrontierEndpointError(
                request_id=request_id,
                field="source_ref",
                reason=str(exc),
                status_code=422,
                code="decision_not_asserted",
            ).to_dict(),
        )
    except FrontierEndpointError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())
    response.status_code = 202
    return result
