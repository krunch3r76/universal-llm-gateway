"""Admission gates for team/persona and raw frontier dispatch."""

from __future__ import annotations

import asyncio
import time
import uuid
from functools import partial
from typing import Annotated, Any, Literal, Self

import httpx
from agent_seat.profiles import get_profile
from agent_seat.registry import normalize_agent_slug
from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse
from implement_admission.preflight import (
    DecisionNotAssertedError,
    require_decision_asserted,
)
from implement_admission.source_ref import SourceRefError, parse_source_ref
from pydantic import BaseModel, Field, model_validator
from transport_utils import DEFAULT_STARGATE_URL, make_async_client
from universal_logging import get_logger

from .admission import (
    is_cursor_sdk_generate_role,
    resolve_handoff_seat,
    resolve_handoff_target,
)
from .closeout_reply import parse_closeout_payload, run_implement_closeout_pipeline
from .contract_derivation import derive_contract
from .cursor_sdk_generate import dispatch_cursor_sdk_generate
from .dispatch_thread_context import as_user_message, read_latest_dispatch_thread_body
from .events import (
    FrontierHandoffCreated,
    FrontierHandoffDeprecatedAlias,
    FrontierHandoffExecutorOverride,
    FrontierHandoffMaterializationIncomplete,
    FrontierHandoffPacketEnriched,
    FrontierHandoffRequested,
)
from .executor_resolution import (
    _read_packet_executor_inputs,
    should_emit_executor_override_audit,
)
from .generate_wrap import dispatch_cursor_sdk_generate_route
from .handoff import (
    _resolve_packet_file,
    _workspaces_root,
    build_pointer_body,
    check_contract_ambiguity,
    create_handoff_thread,
    validate_packet,
)
from .handoff_response import (
    build_handoff_result,
    build_push_reminder,
    build_recommended_executor,
    build_recommended_review,
    build_seat_capability,
)
from .implement_admission_bridge import (
    BridgeResult,
    StargateCortexReader,
    _executor_probe_root,
    resolve_source_ref_to_packet,
    verify_both_present_hash,
)
from .handoff_packet_enrich import WEB_RECEIVER_AGENT, enrich_web_handoff_packet
from .implement_ready_gate import require_implement_ready
from .service import (
    FrontierEndpointError,
    FrontierGenerateRequest,
    build_dispatch_body,
)

team_router = APIRouter(prefix="/api/v1/team", tags=["team"])
frontier_router = APIRouter(prefix="/api/v1/frontier", tags=["frontier"])
implement_router = APIRouter(prefix="/api/v1/implement", tags=["implement"])
logger = get_logger(__name__)

_FORWARD_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=15.0, pool=5.0)


# ---- dispatch-surface-split Phase 1: op-discriminated body models ----


class _DispatchCommon(BaseModel):
    """Shared fields across all op variants — not instantiated directly."""

    model_config = {"extra": "forbid"}

    system: str = ""
    reasoning_effort: str | None = None
    generation_options: dict[str, Any] | None = None
    max_tool_turns: int | None = None
    transcript_id: str | None = None
    remote_mcp: bool | None = None
    caller_agent: str | None = None
    timeout_seconds: int | None = Field(default=None, gt=0, le=86_400)
    bus_lifecycle: Literal["persistent", "ephemeral"] | None = None


class TeamDispatchGenerateBody(_DispatchCommon):
    """``team_dispatch`` with ``op="generate"`` — default bus thread delivery.

    API functional roles auto-provision an agent-bus thread and admit
    ``output_contract=thread`` (poll ``poll_hint``, not inline-only
    ``pipeline(op=result)``). ``cursor-sdk`` uses the dedicated SDK orchestrator.

    ``dispatch_thread_id`` binds server-owned thread persistence on the
    team-dispatch pipeline (distinct from ``transcript_id`` provenance-only).
    """

    op: Literal["generate"]
    role: str
    dispatch_thread_id: str | None = None
    model: str | None = None
    # Caller inline-intent knob. ``None`` keeps the team default (tools-on for
    # tool-capable families); ``False`` requests an inline-only generation (no
    # client-side MCP loop and, for Anthropic, no server-side remote MCP),
    # closing the forced-remote-MCP hang (thread 1653).
    mcp: bool | None = None
    packet_path: str | None = None
    source_ref: str | None = None
    # When set and packet_path is absent (contract=implement|wrap), the server
    # materializes the six-block packet via resolve_source_ref_to_packet
    # (first-class wrap). Grammar: todo:/plan:/plan_phase:/agent-bus:/packet:.
    contract: Literal["light-bounded", "pure-mechanical", "implement", "wrap"]
    reuse_thread: str | None = None
    density_triage: (
        Literal[
            "judgment_required",
            "cross_cutting",
            "dispatch_surface",
            "admission_path",
            "trivial",
        ]
        | None
    ) = None
    review_opt_out_reason_code: (
        Literal[
            "routine_single_subsystem",
            "suggestion_only_first_pass",
            "cost_exceeds_false_negative_risk",
        ]
        | None
    ) = None
    auto_review_child: bool = False
    # thread / subject MUST NOT appear — extra="forbid" rejects any caller that
    # supplies them (schema-level enforcement per Phase 0 contract).

    @model_validator(mode="after")
    def _require_dispatch_thread_id_unless_wrap(self) -> Self:
        if self.contract != "wrap":
            if not self.dispatch_thread_id or not self.dispatch_thread_id.strip():
                raise ValueError(
                    "dispatch_thread_id is required when contract is not wrap"
                )
        return self

    @model_validator(mode="after")
    def _validate_wrap_contract_inputs(self) -> Self:
        if self.contract != "wrap":
            return self
        if self.packet_path is not None:
            raise ValueError("wrap_with_packet_path")
        if self.source_ref is None:
            raise ValueError("wrap_requires_source_ref")
        if self.density_triage is not None:
            raise ValueError("wrap_field_not_applicable:density_triage")
        if self.review_opt_out_reason_code is not None:
            raise ValueError("wrap_field_not_applicable:review_opt_out_reason_code")
        if self.auto_review_child:
            raise ValueError("wrap_field_not_applicable:auto_review_child")
        return self


class TeamDispatchToThreadBody(_DispatchCommon):
    """``team_dispatch`` with ``op="to_thread"`` — result posted to agent-bus thread.

    ``role`` selects a ``role:{slug}`` execution contract.

    ``thread`` is the agent-bus delivery target. ``dispatch_thread_id`` is the
    cortex compaction key (orthogonal — do not conflate the two).
    """

    op: Literal["to_thread"]
    role: str
    dispatch_thread_id: str
    thread: str
    subject: str | None = None
    model: str | None = None
    # Caller inline-intent knob (see ``TeamDispatchGenerateBody.mcp``).
    mcp: bool | None = None
    contract: Literal["light-bounded", "pure-mechanical", "implement"]
    auto_review_child: bool = False
    # result_delivery MUST NOT appear — derived from thread + role; extra="forbid"
    # rejects any caller that supplies it.


# FastAPI resolves the union via the ``op`` discriminator key.
TeamDispatchBody = Annotated[
    TeamDispatchGenerateBody | TeamDispatchToThreadBody,
    Field(discriminator="op"),
]


class _FrontierDispatchCommon(_DispatchCommon):
    """Persona-free frontier dispatch still accepts OpenAI-shaped messages."""

    messages: list[dict[str, Any]]


class FrontierDispatchGenerateBody(_FrontierDispatchCommon):
    """``POST /api/v1/frontier/dispatch`` with ``op="generate"`` — persona-free."""

    op: Literal["generate"]
    model: str
    # ``mcp`` knob is exposed only on the persona-free HTTP surface. Default is
    # False — canonical persona-free use is one-shot inline-substrate reasoning.
    # Pass True to enable the MCP tool loop. Agents: prefer MCP ``team_dispatch``.
    mcp: bool = False


class FrontierDispatchToThreadBody(_FrontierDispatchCommon):
    """``POST /api/v1/frontier/dispatch`` with ``op="to_thread"`` — persona-free."""

    op: Literal["to_thread"]
    model: str
    thread: str
    subject: str | None = None
    mcp: bool = False


FrontierDispatchBody = Annotated[
    FrontierDispatchGenerateBody | FrontierDispatchToThreadBody,
    Field(discriminator="op"),
]


def _normalize_op_body(
    body: (
        TeamDispatchGenerateBody
        | TeamDispatchToThreadBody
        | FrontierDispatchGenerateBody
        | FrontierDispatchToThreadBody
    ),
    *,
    source_text: str | None = None,
) -> dict[str, Any]:
    """Translate a discriminated dispatch body into ``FrontierGenerateRequest``
    kwargs.
    """
    common: dict[str, Any] = {
        "messages": as_user_message(source_text)
        if source_text is not None
        else getattr(body, "messages", []),
        "system": body.system,
        "reasoning_effort": body.reasoning_effort,
        "generation_options": body.generation_options,
        "max_tool_turns": body.max_tool_turns,
        "transcript_id": body.transcript_id,
        "remote_mcp": body.remote_mcp,
        "caller_agent": body.caller_agent,
        "timeout_seconds": body.timeout_seconds,
        "bus_lifecycle": body.bus_lifecycle,
    }

    # Carry role / model / mcp depending on variant. ``mcp`` is honored on every
    # variant that declares it: the frontier (role-free) surface defaults it to
    # False, the team variants default it to None (team default tools-on, unless
    # the caller opts out) — see service.build_dispatch_body + admission.
    if hasattr(body, "role"):
        common["role"] = body.role
    if hasattr(body, "dispatch_thread_id"):
        common["dispatch_thread_id"] = body.dispatch_thread_id
    if hasattr(body, "model"):
        common["model"] = body.model
    if hasattr(body, "mcp"):
        common["mcp"] = body.mcp
    if hasattr(body, "contract"):
        common["resolved_contract"] = body.contract
    if hasattr(body, "density_triage"):
        common["density_triage"] = body.density_triage
    if hasattr(body, "review_opt_out_reason_code"):
        common["review_opt_out_reason_code"] = body.review_opt_out_reason_code
    if hasattr(body, "auto_review_child"):
        common["auto_review_child"] = body.auto_review_child

    if body.op == "generate":
        common["output_contract"] = "inline"
        common["op"] = "generate"
        return common

    # op == "to_thread"
    thread: str = body.thread  # type: ignore[attr-defined]
    common["output_contract"] = "thread"
    common["target_thread"] = thread
    common["op"] = "to_thread"
    subject: str | None = getattr(body, "subject", None)
    if subject is not None:
        common["reply_subject"] = subject
    return common


async def _dispatch(
    req: FrontierGenerateRequest, response: Response
) -> dict[str, Any] | JSONResponse:
    try:
        from systems.proxy.dependencies import get_proxy

        proxy = get_proxy()
        event_bus = getattr(proxy, "event_bus", None)

        def _publish_event(event: Any) -> None:
            if event_bus is None:
                return
            event_bus.publish_from_sync(event)

        dispatch_body = await build_dispatch_body(req, event_publisher=_publish_event)
        pipeline_opts = dispatch_body.get("pipeline_options", {})
        preview = pipeline_opts.pop("_knob_resolution_preview", None)
        capability_preview = pipeline_opts.pop("_capability_preview", None)
    except FrontierEndpointError as exc:
        logger.warning(
            "dispatch rejected: request_id=%s field=%s reason=%s",
            exc.request_id,
            exc.field,
            exc.reason,
        )
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    async with make_async_client(
        DEFAULT_STARGATE_URL, timeout=_FORWARD_TIMEOUT
    ) as client:
        forward = await client.post("/api/v1/pipelines/dispatch", json=dispatch_body)

    response.status_code = forward.status_code
    try:
        result = forward.json()
    except ValueError as exc:
        logger.error(
            "dispatch forward returned non-JSON: status=%s error=%s",
            forward.status_code,
            exc,
        )
        return {
            "error": {
                "code": "dispatch_invalid_response",
                "message": forward.text[:500],
            }
        }
    if isinstance(result, dict):
        if preview is not None:
            result["knob_resolution"] = preview
        if capability_preview is not None:
            result["capabilities"] = capability_preview
    return result


# ---- dispatch-surface-split Phase 1: op-discriminated routes ----


@team_router.post("/dispatch", status_code=202, response_model=None)
async def team_dispatch(
    body: TeamDispatchBody,
    response: Response,
) -> dict[str, Any] | JSONResponse:
    """Persona-required dispatch with explicit op discrimination.

    Two ops:
    - ``op="generate"``: API roles auto-provision bus thread + admit
      ``output_contract=thread``; poll ``poll_hint`` (agent-bus wait).
    - ``op="to_thread"``: caller-owned ``thread``; reply lands on bus after
      dispatch completes.

    Agents use MCP ``team_dispatch`` for all consult surfaces. This HTTP route
    is for Stargate-internal and pipeline-composition callers.
    """
    request_id = uuid.uuid4().hex[:12]
    role = getattr(body, "role", None)
    if (
        body.op == "generate"
        and role is not None
        and is_cursor_sdk_generate_role(role, request_id=request_id)
    ):
        return await dispatch_cursor_sdk_generate_route(
            request_id=request_id,
            body=body,
            role=role,
            response=response,
        )

    if (
        body.op == "generate"
        and getattr(body, "contract", None) == "wrap"
        and role is not None
        and not is_cursor_sdk_generate_role(role, request_id=request_id)
    ):
        return JSONResponse(
            status_code=422,
            content=FrontierEndpointError(
                request_id=request_id,
                field="role",
                reason=(
                    "contract=wrap is only admitted on the cursor-sdk generate branch"
                ),
                status_code=422,
                code="wrap_role_not_admitted",
            ).to_dict(),
        )

    if body.op == "generate" and role is not None:
        from .api_role_generate import dispatch_api_role_generate

        try:
            result = await dispatch_api_role_generate(
                request_id=request_id,
                body=body,
                response=response,
            )
        except FrontierEndpointError as exc:
            return JSONResponse(status_code=exc.status_code, content=exc.to_dict())
        if isinstance(result, dict):
            response.status_code = 202
        return result

    source_text = await read_latest_dispatch_thread_body(
        request_id=request_id,
        dispatch_thread_id=body.dispatch_thread_id,
    )
    req = FrontierGenerateRequest(**_normalize_op_body(body, source_text=source_text))
    return await _dispatch(req, response)


@frontier_router.post("/dispatch", status_code=202, response_model=None)
async def frontier_dispatch(
    body: FrontierDispatchBody,
    response: Response,
) -> dict[str, Any] | JSONResponse:
    """Persona-free HTTP dispatch (internal / pipeline composition).

    Two ops:
    - ``op="generate"``: returns admission record; poll
      ``pipeline(op="result", execution_id=…)`` for content.
    - ``op="to_thread"``: admits dispatch; model's reply lands on ``thread``.

    Agents: use MCP ``team_dispatch`` (``role=synthesizer`` for inline-only,
    API roles for tool-loop consults). ``POST /api/v1/team/dispatch`` is the
    role-envelope HTTP twin of MCP ``team_dispatch``.
    """
    req = FrontierGenerateRequest(**_normalize_op_body(body))
    return await _dispatch(req, response)


# ---- handoff: synchronous web-seat agent-bus thread creation ----


class TeamHandoffBody(BaseModel):
    """``POST /api/v1/team/handoff`` — create an agent-bus thread for a manual seat.

    ``seat`` — manual seat slug (``claude-web``, ``claude-cursor``, roster aliases).
    ``role`` — handoff roster slug (``web-consult``, ``cursor-implement``, …).
    At least one of ``seat`` or ``role`` is required. Contract is derived server-side
    (F1: ``source_ref`` dispatch_lane → packet front-matter → role default → consult).

    At least one of ``source_ref`` or ``packet_path`` must be present.
    ``source_ref`` triggers normalize→materialize (Phase 2 unified admission).
    ``contract`` — optional explicit authority grant (``consult`` | ``implement``);
    highest-priority signal in F1 derivation when set.
    """

    model_config = {"extra": "forbid"}

    op: Literal["handoff"]
    role: str | None = None
    seat: str | None = None
    packet_path: str | None = None
    source_ref: str | None = None
    contract: Literal["light-bounded", "pure-mechanical", "implement"] | None = None
    executor_override: str | None = None
    executor_override_reason_code: str | None = None
    executor_override_reason: str | None = None
    subject: str
    pointer_body: str | None = None
    tags: list[str] | None = None
    caller_agent: str | None = None
    bus_lifecycle: Literal["persistent", "ephemeral"] | None = None


@team_router.post("/handoff", status_code=200, response_model=None)
async def team_handoff(
    body: TeamHandoffBody,
    response: Response,
) -> dict[str, Any] | JSONResponse:
    """Create a manual-seat handoff thread; return thread_id synchronously.

    No model dispatch. Web seats: operator pushes agent-bus. Cursor seats:
    operator opens the thread in the IDE. Close your turn with ``push_reminder``.
    """
    request_id = uuid.uuid4().hex[:12]

    if body.source_ref is None and body.packet_path is None:
        return JSONResponse(
            status_code=422,
            content=FrontierEndpointError(
                request_id=request_id,
                field="source_ref",
                reason=(
                    "At least one of source_ref or packet_path is required "
                    "for handoff admission"
                ),
                status_code=422,
                code="handoff_input_underspecified",
            ).to_dict(),
        )

    if body.seat is None and body.role is None:
        return JSONResponse(
            status_code=422,
            content=FrontierEndpointError(
                request_id=request_id,
                field="seat",
                reason="At least one of seat or role is required for handoff admission",
                status_code=422,
                code="handoff_seat_underspecified",
            ).to_dict(),
        )

    loop = asyncio.get_running_loop()
    workspaces_root = _workspaces_root()
    reader = StargateCortexReader()

    packet_path = body.packet_path
    caller_packet_path = body.packet_path
    implement_spec_hash_value: str | None = None
    warnings: list[str] = []
    frontmatter_source_ref: str | None = None
    materialization_present: bool | None = None

    try:
        await loop.run_in_executor(
            None,
            partial(require_decision_asserted, cortex=reader),
        )

        if body.source_ref is not None:
            if packet_path is not None:
                verify_result = await loop.run_in_executor(
                    None,
                    partial(
                        verify_both_present_hash,
                        request_id=request_id,
                        source_ref=body.source_ref,
                        packet_path=packet_path,
                        cortex=reader,
                        workspaces_root=workspaces_root,
                        author_family=body.caller_agent,
                    ),
                )
                implement_spec_hash_value = verify_result.implement_spec_hash
                warnings.extend(verify_result.warnings)
            else:
                bridge_result: BridgeResult = await loop.run_in_executor(
                    None,
                    partial(
                        resolve_source_ref_to_packet,
                        body.source_ref,
                        cortex=reader,
                        workspaces_root=workspaces_root,
                        request_id=request_id,
                        author_family=body.caller_agent,
                    ),
                )
                if bridge_result.gated:
                    return {
                        "status": "gated",
                        "gated_reason": bridge_result.gated_reason,
                        "source_ref": body.source_ref,
                        "thread_id": None,
                    }
                packet_path = bridge_result.packet_path
                implement_spec_hash_value = bridge_result.implement_spec_hash
                materialization_present = bridge_result.materialization_present
                warnings.extend(bridge_result.warnings)

        assert packet_path is not None

        from systems.proxy.dependencies import get_proxy

        proxy = get_proxy()
        event_bus = getattr(proxy, "event_bus", None)

        def _publish(event: Any) -> None:
            if event_bus is None:
                return
            event_bus.publish_from_sync(event)

        if body.seat is not None:
            if normalize_agent_slug(body.seat) == "cursor-sdk":
                # DEPRECATED ALIAS: op=handoff,seat=cursor-sdk normalizes to the
                # canonical generate path. packet_path is already resolved above
                # (source_ref bridge + `assert packet_path is not None`) and
                # require_decision_asserted has already run, so route straight to
                # the SDK generate orchestrator. It creates the thread and
                # dispatches the worker itself — do NOT duplicate either here.
                result = await dispatch_cursor_sdk_generate(
                    request_id=request_id,
                    role="cursor-sdk",
                    model=None,
                    subject=body.subject,
                    caller_agent=body.caller_agent,
                    contract="implement",
                    packet_path=packet_path,
                    message_text=body.pointer_body,
                    reuse_thread=getattr(body, "reuse_thread", None),
                    bus_lifecycle=getattr(body, "bus_lifecycle", None),
                )
                result["deprecated_alias"] = {
                    "normalized_from": "op=handoff,seat=cursor-sdk",
                    "use_instead": "team_dispatch(op=generate, role=cursor-sdk, …)",
                }
                result["warnings"] = (
                    list(result.get("warnings") or [])
                    + warnings
                    + [
                        "op=handoff,seat=cursor-sdk is deprecated — use "
                        "team_dispatch(op=generate, role=cursor-sdk, …)"
                    ]
                )
                _publish(
                    FrontierHandoffDeprecatedAlias(
                        request_id=request_id,
                        normalized_op="generate",
                        seat="cursor-sdk",
                    )
                )
                return result
            else:
                to_agent, family, platform, resolved_model = resolve_handoff_seat(
                    seat=body.seat,
                    request_id=request_id,
                )
        else:
            to_agent, family, platform, resolved_model = resolve_handoff_target(
                role=body.role or "",
                request_id=request_id,
            )

        handoff_contract, contract_source = await loop.run_in_executor(
            None,
            lambda: derive_contract(
                explicit_contract=body.contract,
                source_ref=body.source_ref,
                packet_path=packet_path,
                role=body.role,
                cortex=reader,
                workspaces_root=workspaces_root,
            ),
        )

        check_contract_ambiguity(
            request_id=request_id,
            packet_path=packet_path,
            contract_source=contract_source,
            workspaces_root=workspaces_root,
        )

        if to_agent == WEB_RECEIVER_AGENT:
            packet_file = _resolve_packet_file(workspaces_root.resolve(), packet_path)
            if packet_file is not None:
                original = packet_file.read_text(encoding="utf-8", errors="replace")
                enrich_result = enrich_web_handoff_packet(
                    original,
                    cortex=reader,
                )
                if enrich_result.changed:
                    packet_file.write_text(enrich_result.text, encoding="utf-8")
                    _publish(
                        FrontierHandoffPacketEnriched(
                            request_id=request_id,
                            packet_path=packet_path,
                            to_agent=to_agent,
                            skills_added=enrich_result.skills_added,
                            skills_already_wired=enrich_result.skills_already_wired,
                            threads_added=enrich_result.threads_added,
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

        _publish(
            FrontierHandoffRequested(
                request_id=request_id,
                role=body.role or "",
                model=resolved_model,
                to_agent=to_agent,
                handoff_contract=handoff_contract,
            )
        )

        if materialization_present is False and body.source_ref is not None:
            _publish(
                FrontierHandoffMaterializationIncomplete(
                    request_id=request_id,
                    packet_path=packet_path,
                    probe_root=str(_executor_probe_root(workspaces_root.resolve())),
                    source_ref=body.source_ref,
                )
            )

        pointer = build_pointer_body(
            request_id=request_id,
            packet_path=packet_path,
            subject=body.subject,
            pointer_body=body.pointer_body,
            handoff_contract=handoff_contract,
            materialization_fallback=materialization_present is False,
            to_agent=to_agent,
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

        _publish(
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
        override_supplied = (
            body.executor_override is not None or fm_override is not None
        )
        if should_emit_executor_override_audit(
            handoff_contract=handoff_contract,
            recommended_executor=executor_fields.get("recommended_executor"),
            override_supplied=override_supplied,
        ):
            _publish(
                FrontierHandoffExecutorOverride(
                    request_id=request_id,
                    handoff_contract=handoff_contract,
                    recommended_executor=executor_fields["recommended_executor"]
                    or "composer",
                    source=executor_fields["recommended_executor_source"] or "",
                    reason_code=executor_fields.get("recommended_executor_reason_code"),
                )
            )

    except SourceRefError as exc:
        logger.warning(
            "handoff rejected: request_id=%s field=source_ref code=%s reason=%s",
            request_id,
            exc.code,
            exc,
        )
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
        logger.warning(
            "handoff rejected: request_id=%s field=source_ref reason=%s",
            request_id,
            exc,
        )
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
        logger.warning(
            "handoff rejected: request_id=%s field=%s reason=%s",
            exc.request_id,
            exc.field,
            exc.reason,
        )
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

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
        **build_handoff_result(thread_id=thread_id, to_agent=to_agent),
        **executor_fields,
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


_CLOSEOUT_DEDUPE_TTL_S = 3600.0
_closeout_dedupe: dict[
    str, float
] = {}  # TODO(durable-dedupe): todo:wire-closeout-trigger-consumer


def _closeout_dedupe_seen(key: str) -> bool:
    """Return True iff key already seen within TTL; otherwise record and return False.

    Synchronous (no await) so check-and-set is atomic on the event loop. In-memory
    only — markers are lost on restart (v1 trade-off, see
    todo:wire-closeout-trigger-consumer).
    """
    now = time.monotonic()
    for k in [
        k for k, ts in _closeout_dedupe.items() if now - ts > _CLOSEOUT_DEDUPE_TTL_S
    ]:
        _closeout_dedupe.pop(k, None)
    if key in _closeout_dedupe:
        return True
    _closeout_dedupe[key] = now
    return False


class ImplementCloseoutBody(BaseModel):
    """Dispatched implement closeout — triggers pipeline:implement-closeout."""

    model_config = {"extra": "forbid"}

    closeout: dict[str, Any]
    source_ref: str | None = None
    idempotency_key: str | None = None


@implement_router.post("/closeout", status_code=200, response_model=None)
async def implement_closeout(
    body: ImplementCloseoutBody,
) -> dict[str, Any] | JSONResponse:
    """Apply ImplementCloseout via pipeline:implement-closeout (Step 4)."""
    request_id = uuid.uuid4().hex[:12]
    payload = parse_closeout_payload(body.closeout)
    if payload is None:
        return JSONResponse(
            status_code=422,
            content=FrontierEndpointError(
                request_id=request_id,
                field="closeout",
                reason="closeout must be a valid ImplementCloseout object",
                status_code=422,
                code="closeout_invalid",
            ).to_dict(),
        )

    # Idempotency dedupe (producer path only; manual callers omit the key).
    if body.idempotency_key and _closeout_dedupe_seen(body.idempotency_key):
        logger.info("closeout deduped: key=%s", body.idempotency_key)
        return {"ok": True, "deduped": True, "idempotency_key": body.idempotency_key}

    # Status presence (ImplementCloseout requires it; fail fast at ingress).
    if "status" not in payload:
        return JSONResponse(
            status_code=422,
            content=FrontierEndpointError(
                request_id=request_id,
                field="closeout",
                reason="closeout.status is required",
                status_code=422,
                code="closeout_invalid",
            ).to_dict(),
        )

    # source_ref resolvability — required on keyed (producer) path; reject sidecars.
    effective_source_ref = body.source_ref or payload.get("source_ref")
    if body.idempotency_key:
        if effective_source_ref is None:
            logger.warning("closeout source_ref missing: key=%s", body.idempotency_key)
            return JSONResponse(
                status_code=422,
                content=FrontierEndpointError(
                    request_id=request_id,
                    field="source_ref",
                    reason="source_ref is required on the keyed (producer) path",
                    status_code=422,
                    code="closeout_source_unresolvable",
                ).to_dict(),
            )
        try:
            parse_source_ref(effective_source_ref)
        except SourceRefError as exc:
            logger.warning(
                "closeout source_ref unresolvable: key=%s ref=%s err=%s",
                body.idempotency_key,
                effective_source_ref,
                exc,
            )
            return JSONResponse(
                status_code=422,
                content=FrontierEndpointError(
                    request_id=request_id,
                    field="source_ref",
                    reason=f"source_ref not adapter-resolvable: {effective_source_ref}",
                    status_code=422,
                    code="closeout_source_unresolvable",
                ).to_dict(),
            )

    loop = asyncio.get_running_loop()
    logger.info(
        "closeout accepted: key=%s ref=%s",
        body.idempotency_key,
        effective_source_ref,
    )
    try:
        result = await loop.run_in_executor(
            None,
            lambda: run_implement_closeout_pipeline(
                payload, source_ref=body.source_ref
            ),
        )
    except Exception as exc:
        logger.warning("implement closeout failed: request_id=%s %s", request_id, exc)
        return JSONResponse(
            status_code=502,
            content=FrontierEndpointError(
                request_id=request_id,
                field="closeout",
                reason=str(exc),
                status_code=502,
                code="closeout_pipeline_error",
            ).to_dict(),
        )
    if not result.get("ok", True) and result.get("error"):
        return JSONResponse(status_code=502, content=result)
    return result
