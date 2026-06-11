"""Admission gates for team/persona and raw frontier dispatch."""

from __future__ import annotations

import asyncio
import uuid
from functools import partial
from typing import Annotated, Any, Literal

import httpx
from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse
from implement_admission.preflight import (
    DecisionNotAssertedError,
    require_decision_asserted,
)
from implement_admission.source_ref import SourceRefError
from pydantic import BaseModel, Field
from transport_utils import DEFAULT_STARGATE_URL, make_async_client
from universal_logging import get_logger

from .admission import (
    resolve_cursor_sdk_handoff_seat,
    resolve_handoff_seat,
    resolve_handoff_target,
)
from .cursor_sdk_worker_dispatch import (
    dispatch_cursor_sdk_worker,
    post_worker_failure_turn,
)
from agent_seat.profiles import get_profile
from agent_seat.registry import normalize_agent_slug
from .closeout_reply import parse_closeout_payload, run_implement_closeout_pipeline
from .contract_derivation import derive_contract
from .events import (
    FrontierHandoffCreated,
    FrontierHandoffExecutorOverride,
    FrontierHandoffMaterializationIncomplete,
    FrontierHandoffRequested,
)
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
from .executor_resolution import (
    _read_packet_executor_inputs,
    should_emit_executor_override_audit,
)
from .implement_admission_bridge import (
    BridgeResult,
    StargateCortexReader,
    _executor_probe_root,
    resolve_source_ref_to_packet,
    verify_both_present_hash,
)
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

    messages: list[dict[str, Any]]
    system: str = ""
    reasoning_effort: str | None = None
    generation_options: dict[str, Any] | None = None
    max_tool_turns: int | None = None
    transcript_id: str | None = None
    remote_mcp: bool | None = None
    caller_agent: str | None = None
    timeout_seconds: int | None = Field(default=None, gt=0, le=86_400)


class TeamDispatchGenerateBody(_DispatchCommon):
    """``team_dispatch`` with ``op="generate"`` — result returned inline via poll.

    ``role`` selects a ``role:{slug}`` execution contract (Phase 5 of the
    agent-naming cleanup arc). Replaces the legacy ``agent`` field.

    ``dispatch_thread_id`` binds server-owned thread persistence on the
    team-dispatch pipeline (distinct from ``transcript_id`` provenance-only).
    """

    op: Literal["generate"]
    role: str
    dispatch_thread_id: str
    model: str | None = None
    # thread / subject MUST NOT appear — extra="forbid" rejects any caller that
    # supplies them (schema-level enforcement per Phase 0 contract).


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
    # result_delivery MUST NOT appear — derived from thread + role; extra="forbid"
    # rejects any caller that supplies it.


# FastAPI resolves the union via the ``op`` discriminator key.
TeamDispatchBody = Annotated[
    TeamDispatchGenerateBody | TeamDispatchToThreadBody,
    Field(discriminator="op"),
]


class FrontierDispatchGenerateBody(_DispatchCommon):
    """``POST /api/v1/frontier/dispatch`` with ``op="generate"`` — persona-free."""

    op: Literal["generate"]
    model: str
    # ``mcp`` knob is exposed only on the persona-free HTTP surface. Default is
    # False — canonical persona-free use is one-shot inline-substrate reasoning.
    # Pass True to enable the MCP tool loop. Agents: prefer MCP ``team_dispatch``.
    mcp: bool = False


class FrontierDispatchToThreadBody(_DispatchCommon):
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
) -> dict[str, Any]:
    """Translate a discriminated dispatch body into ``FrontierGenerateRequest``
    kwargs.
    """
    common: dict[str, Any] = {
        "messages": body.messages,
        "system": body.system,
        "reasoning_effort": body.reasoning_effort,
        "generation_options": body.generation_options,
        "max_tool_turns": body.max_tool_turns,
        "transcript_id": body.transcript_id,
        "remote_mcp": body.remote_mcp,
        "caller_agent": body.caller_agent,
        "timeout_seconds": body.timeout_seconds,
    }

    # Carry role / model / mcp depending on variant. ``mcp`` is exposed only
    # on the frontier (role-free) surface; team variants derive mcp from
    # the role's frontier_kind in service.build_dispatch_body.
    if hasattr(body, "role"):
        common["role"] = body.role
    if hasattr(body, "dispatch_thread_id"):
        common["dispatch_thread_id"] = body.dispatch_thread_id
    if hasattr(body, "model"):
        common["model"] = body.model
    if hasattr(body, "mcp"):
        common["mcp"] = body.mcp

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
    - ``op="generate"``: returns admission record; poll
      ``pipeline(op="result", execution_id=…)`` for content.
    - ``op="to_thread"``: admits dispatch; the agent's reply lands on
      ``thread``; tracker terminal status reflects observed reply (Phase 2).

    Agents use MCP ``team_dispatch`` for all consult surfaces. This HTTP route
    is for Stargate-internal and pipeline-composition callers.
    """
    req = FrontierGenerateRequest(**_normalize_op_body(body))
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
    contract: Literal["consult", "implement"] | None = None
    executor_override: str | None = None
    executor_override_reason_code: str | None = None
    executor_override_reason: str | None = None
    subject: str
    pointer_body: str | None = None
    tags: list[str] | None = None
    caller_agent: str | None = None


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
                to_agent, family, platform, resolved_model = (
                    resolve_cursor_sdk_handoff_seat(
                        body.seat,
                        request_id=request_id,
                    )
                )
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

        is_cursor_sdk = to_agent == "cursor-sdk"

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
        )

        thread_id = await create_handoff_thread(
            request_id=request_id,
            to_agent=to_agent,
            subject=body.subject,
            pointer_body=pointer,
            caller_agent=body.caller_agent,
            tags=body.tags,
            handoff_contract=handoff_contract,
        )

        if is_cursor_sdk:
            worker_ok, worker_warning = await dispatch_cursor_sdk_worker(
                request_id=request_id,
                thread_id=thread_id,
                model=resolved_model,
                packet_path=packet_path,
            )
            if not worker_ok:
                await post_worker_failure_turn(
                    thread_id=thread_id,
                    request_id=request_id,
                )
                warnings.append(worker_warning or "worker_dispatch: failed")

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


class ImplementCloseoutBody(BaseModel):
    """Dispatched implement closeout — triggers pipeline:implement-closeout."""

    model_config = {"extra": "forbid"}

    closeout: dict[str, Any]
    source_ref: str | None = None


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
    loop = asyncio.get_running_loop()
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
