"""Frontier-generate endpoint orchestration and persona admission checks."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from agent_seat import AgentMeta, assemble_system_prompt, hydrate_agent
from agent_seat.body_injection import INJECTED_BODY_BUDGET_BYTES
from agent_seat.role_entity_sync import resolve_dispatch_capabilities
from llm_adapters.capability_dispatch import project_knob_resolution
from model_id import (
    WireModelResolutionError,
    canonical_model_entity_id,
    resolve_wire_model_id,
)

from .admission import (
    EventPublisher,
    FrontierEndpointError,
    emit_rejection,
    enforce_model,
    enforce_options,
    enforce_team_dispatch_generate_admit,
    is_chat_completions_only,
    mcp_enabled_for_frontier_dispatch,
    mcp_enabled_for_team_dispatch,
    verify_thread_writable,
)
from .dispatch_messages import extract_last_user_message, wire_latest_user_turn
from .events import (
    FrontierEndpointPersonaResolved,
    FrontierEndpointRequested,
    InlineBodyInjectionResolved,
)

_FRONTIER_DISPATCH_PIPELINE_ID = "frontier-dispatch"
_TEAM_DISPATCH_PIPELINE_ID = "team-dispatch"


@dataclass(slots=True)
class FrontierGenerateRequest:
    messages: list[dict[str, Any]]
    model: str | None = None
    role: str | None = None
    system: str = ""
    mcp: bool | None = None
    reasoning_effort: str | None = None
    generation_options: dict[str, Any] | None = None
    max_tool_turns: int | None = None
    transcript_id: str | None = None
    dispatch_thread_id: str | None = None
    remote_mcp: bool | None = None
    caller_agent: str | None = None
    timeout_seconds: int | None = None
    # dispatch-surface-split Phase 1: explicit op discrimination
    output_contract: Literal["inline", "thread"] = "inline"
    target_thread: str | None = None
    op: Literal["generate", "to_thread"] | None = None
    # On-behalf delivery (2026-05-22) — caller-supplied subject for the
    # reply turn posted by Stargate. None ⇒ delivery handler auto-derives.
    reply_subject: str | None = None
    # Override post-delivery thread close for ``op="to_thread"``. None ⇒ ephemeral.
    bus_lifecycle: Literal["persistent", "ephemeral"] | None = None
    resolved_contract: str | None = None
    density_triage: str | None = None
    review_opt_out_reason_code: str | None = None
    auto_review_child: bool = False


async def build_dispatch_body(
    req: FrontierGenerateRequest, event_publisher: EventPublisher | None = None
) -> dict[str, Any]:
    """Apply role rules and shape dispatch JSON for ``/pipelines/dispatch``.

    Phase 5: ``req.role`` selects a ``role:{slug}`` execution contract loaded
    by ``hydrate_agent`` (which fetches the role: entity per the updated
    ``_SELF_ENTITY`` map). The internal ``agent=`` keyword to event payloads
    and to ``hydrate_agent`` retains its historical name (event observability
    schema preservation); only the public dispatch parameter is renamed.
    """
    request_id = uuid.uuid4().hex[:12]
    if event_publisher is not None:
        event_publisher(
            FrontierEndpointRequested(
                request_id=request_id,
                agent=req.role,
                model=req.model,
            )
        )

    meta = AgentMeta()
    system_assembled = req.system or ""

    if req.role:
        enforce_team_dispatch_generate_admit(
            req.role,
            request_id=request_id,
            event_publisher=event_publisher,
        )
        # Soft boot: team_dispatch and persona-free frontier HTTP dispatches use the
        # lightweight profile by default. Drops deadlines + review-queue
        # fetches; keeps a 3-reflection floor. The pipeline-handler hydration
        # in resolve_dispatch_tool_set must mirror this profile to avoid the
        # final dispatched prompt regaining a heavy briefing card.
        bundle = await hydrate_agent(req.role, profile="light", model=req.model)
        meta = bundle.agent_meta
        if event_publisher is not None:
            event_publisher(
                FrontierEndpointPersonaResolved(
                    request_id=request_id,
                    agent=req.role,
                    frontier_kind=meta.frontier_kind,
                    default_model=meta.default_model,
                    allowed_models_count=len(meta.allowed_models),
                    allowed_options_count=(
                        len(meta.allowed_options)
                        if meta.allowed_options is not None
                        else None
                    ),
                )
            )
        if bundle.required_body_unresolved:
            raise FrontierEndpointError(
                request_id=request_id,
                field="injected_bodies",
                reason="required conduct rule body failed to resolve",
            )
        system_assembled = assemble_system_prompt(
            req.role,
            briefing_card_md=bundle.briefing_card_md,
            continuation_md=bundle.continuation_md,
            extra_system=req.system,
            inline_only=bundle.inline_only,
            injected_bodies_md=bundle.injected_bodies_md,
        )
        if bundle.inline_only and event_publisher is not None:
            meta_inj = bundle.injection_meta or {}
            metrics = meta_inj.get("metrics") or {}
            injected = meta_inj.get("injected") or []
            event_publisher(
                InlineBodyInjectionResolved(
                    request_id=request_id,
                    seat=req.role,
                    model=req.model or meta.default_model,
                    injected=injected,
                    dropped=meta_inj.get("dropped") or [],
                    total_bytes=sum(
                        int(item.get("bytes", 0))
                        for item in injected
                        if isinstance(item, dict)
                    ),
                    budget_bytes=INJECTED_BODY_BUDGET_BYTES,
                    cache_hit=bool(metrics.get("cache_hit")),
                    cold_fetches=int(metrics.get("cold_fetches", 0)),
                    elapsed_ms=int(metrics.get("elapsed_ms", 0)),
                    deadline_hit=bool(metrics.get("deadline_hit")),
                )
            )

    effective_model = req.model or meta.default_model
    if not effective_model:
        raise FrontierEndpointError(
            request_id=request_id,
            field="model",
            reason="model is required when no role default is configured",
        )
    try:
        effective_model = resolve_wire_model_id(
            effective_model, require_cloud=True
        ).wire_id
    except WireModelResolutionError as exc:
        raise FrontierEndpointError(
            request_id=request_id,
            field="model",
            reason=str(exc),
        ) from exc

    model_entity_id = canonical_model_entity_id(effective_model)
    enforce_model(
        request_id=request_id,
        agent=req.role,
        model=effective_model,
        meta=meta,
        explicit_model=req.model is not None,
        event_publisher=event_publisher,
    )
    if is_chat_completions_only(effective_model):
        raise emit_rejection(
            request_id=request_id,
            agent=req.role,
            field="model",
            reason=(
                f"{effective_model!r} is a Chat Completions-only model — "
                "it is unavailable on the OpenAI Responses API that "
                "team_dispatch and frontier-dispatch pipeline steps use. "
                f"Use llm_generate(model={effective_model!r}, messages=...) instead "
                "(note: llm_generate has a narrower surface — no role, tools, "
                "or transcript_id)."
            ),
            event_publisher=event_publisher,
        )
    generation_options = dict(req.generation_options or {})
    if req.reasoning_effort is not None:
        generation_options.setdefault("reasoning_effort", req.reasoning_effort)
    if "max_tool_turns" in generation_options:
        raise FrontierEndpointError(
            request_id=request_id,
            field="generation_options.max_tool_turns",
            reason=(
                "'max_tool_turns' inside generation_options is not supported — "
                "use the typed top-level parameter instead"
            ),
        )
    enforce_options(
        request_id=request_id,
        agent=req.role,
        opts=generation_options,
        meta=meta,
        event_publisher=event_publisher,
    )
    _eff = generation_options.get("reasoning_effort")
    _eff = _eff if isinstance(_eff, str) and _eff else None
    _maxt = generation_options.get("max_tokens")
    _knob_resolution_preview = project_knob_resolution(
        resolved_model=effective_model,
        requested_effort=_eff,
        requested_max_output=_maxt if isinstance(_maxt, int) else None,
    )
    if req.role is not None:
        mcp_enabled = mcp_enabled_for_team_dispatch(effective_model, req.mcp)
    else:
        mcp_enabled = mcp_enabled_for_frontier_dispatch(effective_model, req.mcp)

    capability_preview: dict[str, Any] | None = None
    if req.role is not None:
        # Echo single-sourced with the pipeline gate: passing the effective
        # ``mcp_enabled`` (post caller-knob) means the transparency surface and
        # ``pipeline_options["mcp"]`` derive from one value and cannot drift.
        capability_preview = resolve_dispatch_capabilities(
            model=effective_model, mcp_enabled=mcp_enabled
        )
        capability_preview["role"] = req.role

    pipeline_options: dict[str, Any] = {
        "model": effective_model,
        "model_entity_id": model_entity_id,
        "system": system_assembled,
        "generation_parameters": generation_options,
        "mcp": mcp_enabled,
        "_endpoint_request_id": request_id,
        "_knob_resolution_preview": _knob_resolution_preview,
        "output_contract": req.output_contract,
    }
    if capability_preview is not None:
        pipeline_options["_capability_preview"] = capability_preview
    if req.role:
        pipeline_options["role"] = req.role
    if req.max_tool_turns is not None:
        pipeline_options["max_tool_turns"] = req.max_tool_turns
    elif req.role is not None:
        pipeline_options["max_tool_turns"] = 100
    if req.remote_mcp is not None:
        pipeline_options["remote_mcp"] = req.remote_mcp

    if req.output_contract == "thread" and req.target_thread:
        _agent_bus_token = os.getenv("AGENT_BUS_TOKEN", "").strip()
        _allow_unset = os.getenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        if not _agent_bus_token and not _allow_unset:
            raise FrontierEndpointError(
                request_id=request_id,
                field="thread",
                reason=(
                    "AGENT_BUS_TOKEN is not configured; thread output contract "
                    "requires agent-bus verification before dispatch. "
                    "Set AGENT_BUS_TOKEN in the Stargate environment, or "
                    "ALLOW_UNSET_AGENT_BUS_TOKEN=true for explicit local bypass."
                ),
                status_code=503,
            )
        if _agent_bus_token:
            await verify_thread_writable(
                req.target_thread,
                request_id=request_id,
                auth_token=_agent_bus_token,
            )

    pipeline_id = (
        _TEAM_DISPATCH_PIPELINE_ID if req.role else _FRONTIER_DISPATCH_PIPELINE_ID
    )

    if req.role:
        dispatch_thread_id = (req.dispatch_thread_id or "").strip()
        if not dispatch_thread_id:
            raise FrontierEndpointError(
                request_id=request_id,
                field="dispatch_thread_id",
                reason=(
                    "dispatch_thread_id is required for team_dispatch — "
                    "binds server-owned assemble/archive on "
                    "thread:dispatch:{dispatch_thread_id}"
                ),
            )
        last_user = extract_last_user_message(req.messages)
        if not last_user:
            raise FrontierEndpointError(
                request_id=request_id,
                field="messages",
                reason="At least one non-empty user message is required",
            )
        wire_messages = wire_latest_user_turn(req.messages)
    else:
        dispatch_thread_id = None
        wire_messages = req.messages

    body: dict[str, Any] = {
        "model": pipeline_id,
        "messages": wire_messages,
        "pipeline_options": pipeline_options,
        "output_contract": req.output_contract,
    }
    if req.timeout_seconds is not None:
        body["timeout_seconds"] = req.timeout_seconds
    if req.caller_agent:
        body["caller_agent"] = req.caller_agent
    if dispatch_thread_id:
        body["dispatch_thread_id"] = dispatch_thread_id
    if req.transcript_id:
        body["caller_transcript_id"] = req.transcript_id
    if req.target_thread is not None:
        body["target_thread"] = req.target_thread
    if req.op is not None:
        body["op"] = req.op
    if req.resolved_contract is not None:
        body["resolved_contract"] = req.resolved_contract
    if req.op == "to_thread":
        if req.role:
            body["from_agent"] = req.role
        else:
            model_short = effective_model.replace("/", ":")
            body["from_agent"] = f"frontier:{model_short}"
    if req.reply_subject is not None:
        body["reply_subject"] = req.reply_subject
    if req.bus_lifecycle is not None:
        body["bus_lifecycle"] = req.bus_lifecycle
    return body
