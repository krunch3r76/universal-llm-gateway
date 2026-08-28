"""Endpoint-level signals for team/frontier generate routes."""

from __future__ import annotations

from typing import Literal

from universal_event_bus import Event, event_factory


@event_factory
def FrontierEndpointRequested(  # noqa: N802
    request_id: str,
    agent: str | None,
    model: str | None,
) -> Event:
    """Endpoint admission for team/frontier generate routes.

    Role-vs-direct is encoded by ``agent``: if non-null, this was a
    role-based team dispatch; if null, this was the direct frontier dispatch path.

    ``has_tools`` field retired with the public ``tools`` parameter
    (todo:retire-tools-param-from-dispatch-mcp-surface) — the field could
    no longer fire True after the surface graduation.
    """
    return Event(
        signal="frontier.endpoint.requested",
        payload={
            "request_id": request_id,
            "agent": agent,
            "model": model,
        },
        scope="node",
    )


@event_factory
def FrontierEndpointPersonaResolved(  # noqa: N802
    request_id: str,
    agent: str,
    frontier_kind: str | None,
    default_model: str | None,
    allowed_models_count: int,
    allowed_options_count: int | None,
) -> Event:
    """Persona resolution at /team/dispatch and /frontier/dispatch HTTP endpoints.

    tools_count retired per todo:retire-tools-allowlist-as-caller-concern.
    """
    return Event(
        signal="frontier.endpoint.persona.resolved",
        payload={
            "request_id": request_id,
            "agent": agent,
            "frontier_kind": frontier_kind,
            "default_model": default_model,
            "allowed_models_count": allowed_models_count,
            "allowed_options_count": allowed_options_count,
        },
        scope="node",
    )


@event_factory
def FrontierEndpointRejected(  # noqa: N802
    request_id: str,
    agent: str | None,
    field: str,
    reason: str,
) -> Event:
    return Event(
        signal="frontier.endpoint.option.rejected",
        payload={
            "request_id": request_id,
            "agent": agent,
            "field": field,
            "reason": reason,
        },
        scope="node",
    )


@event_factory
def FrontierHandoffRequested(  # noqa: N802
    request_id: str,
    role: str,
    to_agent: str,
    handoff_contract: str | None = None,
    model: str | None = None,
) -> Event:
    """Handoff admission — seat resolved, thread creation pending.

    ``handoff_contract`` is the resolved work-intent (``consult`` | ``implement``).
    ``model`` is the canonical synthetic seat slug when ``model`` was the selector.
    """
    payload: dict[str, str | None] = {
        "request_id": request_id,
        "role": role,
        "to_agent": to_agent,
        "handoff_contract": handoff_contract,
    }
    if model:
        payload["model"] = model
    return Event(
        signal="frontier.handoff.requested",
        payload=payload,
        scope="node",
    )


@event_factory
def FrontierHandoffExecutorOverride(  # noqa: N802
    request_id: str,
    handoff_contract: str,
    recommended_executor: str,
    source: str,
    reason_code: str | None = None,
) -> Event:
    """Audit when implement handoff resolves a non-default executor advisory."""
    return Event(
        signal="frontier.handoff.executor.override",
        payload={
            "request_id": request_id,
            "handoff_contract": handoff_contract,
            "recommended_executor": recommended_executor,
            "source": source,
            "reason_code": reason_code,
        },
        scope="node",
    )


@event_factory
def FrontierHandoffCreated(  # noqa: N802
    request_id: str,
    to_agent: str,
    thread_id: str,
    reused: bool = False,
) -> Event:
    """Handoff thread created on agent-bus."""
    return Event(
        signal="frontier.handoff.created",
        payload={
            "request_id": request_id,
            "to_agent": to_agent,
            "thread_id": thread_id,
            "reused": reused,
        },
        scope="node",
    )


@event_factory
def FrontierSdkGenerateRequested(  # noqa: N802
    request_id: str,
    role: str,
    execution_id: str,
    handoff_contract: str,
    resolved_model: str,
) -> Event:
    """SDK generate admitted — bypassing cloud pipeline."""
    return Event(
        signal="frontier.sdk.generate.requested",
        payload={
            "request_id": request_id,
            "role": role,
            "execution_id": execution_id,
            "handoff_contract": handoff_contract,
            "resolved_model": resolved_model,
        },
        scope="node",
    )


@event_factory
def FrontierSdkWorkerDispatched(  # noqa: N802
    request_id: str,
    thread_id: str,
    execution_id: str,
    dispatch_id: str | None = None,
    asked_by: str | None = None,
    purpose: str | None = None,
    story_id: str | None = None,
    admitted_via: str | None = None,
    seat: str | None = None,
    nest_under: str | None = None,
    topic: str | None = None,
    packet_kind: str | None = None,
    model_knobs_requested: dict[str, str] | None = None,
) -> Event:
    """SDK worker dispatch accepted.

    Stamps both execution_id and dispatch_id when known so the monitor fold
    keys one row (agent-bus 6164).
    """
    payload: dict[str, object] = {
        "request_id": request_id,
        "thread_id": thread_id,
        "execution_id": execution_id,
    }
    if dispatch_id:
        payload["dispatch_id"] = dispatch_id
    if asked_by is not None:
        payload["asked_by"] = asked_by
    if purpose is not None:
        payload["purpose"] = purpose
    if story_id is not None:
        payload["story_id"] = story_id
    if admitted_via is not None:
        payload["admitted_via"] = admitted_via
    if seat is not None:
        payload["seat"] = seat
    if nest_under is not None:
        payload["nest_under"] = nest_under
    if topic is not None:
        payload["topic"] = topic
    if packet_kind is not None:
        payload["packet_kind"] = packet_kind
    if model_knobs_requested is not None:
        payload["model_knobs_requested"] = model_knobs_requested
    return Event(
        signal="frontier.sdk.worker.dispatched",
        payload=payload,
        scope="node",
    )


@event_factory
def FrontierSdkWorkerDispatchFailed(  # noqa: N802
    request_id: str,
    thread_id: str,
    execution_id: str,
    error: str,
    status_code: int | None = None,
    code: str | None = None,
    blocking_dispatch_id: str | None = None,
    origin_service: str = "stargate",
    schema_version: str = "1",
    failure_layer: str | None = None,
    transport_error_kind: str | None = None,
    dispatch_id: str | None = None,
    detail_summary: str | None = None,
    retryable: bool | None = None,
    http_status: int | None = None,
    worker_error_code: str | None = None,
) -> Event:
    """SDK worker dispatch rejected or unreachable."""
    resolved_http_status = http_status if http_status is not None else status_code
    resolved_worker_code = worker_error_code if worker_error_code is not None else code
    payload: dict[str, object] = {
        "request_id": request_id,
        "thread_id": thread_id,
        "execution_id": execution_id,
        "error": error,
        "origin_service": origin_service,
        "schema_version": schema_version,
    }
    if status_code is not None:
        payload["status_code"] = status_code
    if code is not None:
        payload["code"] = code
    if blocking_dispatch_id is not None:
        payload["blocking_dispatch_id"] = blocking_dispatch_id
    if failure_layer is not None:
        payload["failure_layer"] = failure_layer
    if transport_error_kind is not None:
        payload["transport_error_kind"] = transport_error_kind
    if dispatch_id is not None:
        payload["dispatch_id"] = dispatch_id
    if detail_summary is not None:
        payload["detail_summary"] = detail_summary
    if retryable is not None:
        payload["retryable"] = retryable
    if resolved_http_status is not None:
        payload["http_status"] = resolved_http_status
    if resolved_worker_code is not None:
        payload["worker_error_code"] = resolved_worker_code
    return Event(
        signal="frontier.sdk.worker.failed",
        payload=payload,
        scope="node",
    )


@event_factory
def FrontierSdkWorkerQueued(  # noqa: N802
    request_id: str,
    thread_id: str,
    execution_id: str,
    dispatch_id: str | None = None,
    queue_position: int | None = None,
    asked_by: str | None = None,
    purpose: str | None = None,
    story_id: str | None = None,
    admitted_via: str | None = None,
    seat: str | None = None,
    nest_under: str | None = None,
    topic: str | None = None,
    packet_kind: str | None = None,
    model_knobs_requested: dict[str, str] | None = None,
) -> Event:
    """SDK worker dispatch durably queued awaiting write-lease."""
    payload: dict[str, object] = {
        "request_id": request_id,
        "thread_id": thread_id,
        "execution_id": execution_id,
        "queue_position": queue_position,
    }
    if dispatch_id:
        payload["dispatch_id"] = dispatch_id
    if asked_by is not None:
        payload["asked_by"] = asked_by
    if purpose is not None:
        payload["purpose"] = purpose
    if story_id is not None:
        payload["story_id"] = story_id
    if admitted_via is not None:
        payload["admitted_via"] = admitted_via
    if seat is not None:
        payload["seat"] = seat
    if nest_under is not None:
        payload["nest_under"] = nest_under
    if topic is not None:
        payload["topic"] = topic
    if packet_kind is not None:
        payload["packet_kind"] = packet_kind
    if model_knobs_requested is not None:
        payload["model_knobs_requested"] = model_knobs_requested
    return Event(
        signal="frontier.sdk.worker.queued",
        payload=payload,
        scope="node",
    )


@event_factory
def FrontierHandoffPacketEnriched(  # noqa: N802
    request_id: str,
    packet_path: str,
    to_agent: str,
    skills_added: list[str],
    threads_added: list[str],
    skills_already_wired: list[str] | None = None,
) -> Event:
    """Web handoff packet auto-enriched before validation (assertion #19650)."""
    return Event(
        signal="frontier.handoff.packet.enriched",
        payload={
            "request_id": request_id,
            "packet_path": packet_path,
            "to_agent": to_agent,
            "skills_added": skills_added,
            "skills_already_wired": skills_already_wired or [],
            "threads_added": threads_added,
        },
        scope="node",
    )


@event_factory
def FrontierHandoffMaterializationIncomplete(  # noqa: N802
    request_id: str,
    packet_path: str,
    probe_root: str,
    source_ref: str,
) -> Event:
    """Materialized packet absent at executor workspaces root (G-b probe miss)."""
    return Event(
        signal="frontier.handoff.materialization.incomplete",
        payload={
            "request_id": request_id,
            "packet_path": packet_path,
            "probe_root": probe_root,
            "source_ref": source_ref,
        },
        scope="node",
    )


@event_factory
def FrontierSdkMaterializationIncomplete(  # noqa: N802
    request_id: str,
    packet_path: str,
    probe_root: str,
    source_ref: str,
    execution_id: str | None = None,
    thread_id: str | None = None,
    route: str | None = None,
    origin_service: str = "stargate",
    schema_version: str = "1",
    failure_layer: str = "materialization",
) -> Event:
    """Materialized packet absent at executor workspaces root (SDK generate path)."""
    payload: dict[str, str] = {
        "request_id": request_id,
        "packet_path": packet_path,
        "probe_root": probe_root,
        "source_ref": source_ref,
        "origin_service": origin_service,
        "schema_version": schema_version,
        "failure_layer": failure_layer,
    }
    if execution_id is not None:
        payload["execution_id"] = execution_id
    if thread_id is not None:
        payload["thread_id"] = thread_id
    if route is not None:
        payload["route"] = route
    return Event(
        signal="frontier.sdk.materialization.incomplete",
        payload=payload,
        scope="node",
    )


@event_factory
def DispatchSkillsMounted(  # noqa: N802
    request_id: str,
    role: str | None,
    model: str,
    canonical_slugs: list[str],
    entry_count: int,
    total_bundle_bytes: int,
) -> Event:
    """Skills resolved and mounted at team/frontier dispatch admission."""
    return Event(
        signal="dispatch.skills.mounted",
        payload={
            "request_id": request_id,
            "role": role,
            "model": model,
            "canonical_slugs": canonical_slugs,
            "entry_count": entry_count,
            "total_bundle_bytes": total_bundle_bytes,
        },
        scope="node",
    )


@event_factory
def DispatchSkillsChannelResolved(  # noqa: N802
    request_id: str,
    role: str | None,
    model: str,
    skills: list[dict[str, object]],
) -> Event:
    """Per-skill channel outcome after unified skills= merge and partition."""
    return Event(
        signal="dispatch.skills.channel.resolved",
        payload={
            "request_id": request_id,
            "role": role,
            "model": model,
            "skills": skills,
        },
        scope="node",
    )


@event_factory
def DispatchSkillsPredicatedRejected(  # noqa: N802
    request_id: str,
    role: str | None,
    model: str,
    skills: list[str],
    origin: str,
) -> Event:
    """Caller-supplied MCP-predicated skills rejected on a non-MCP dispatch."""
    return Event(
        signal="dispatch.skills.predicated.rejected",
        payload={
            "request_id": request_id,
            "role": role,
            "model": model,
            "skills": skills,
            "origin": origin,
        },
        scope="node",
    )


@event_factory
def DispatchSkillsInlineInjected(  # noqa: N802
    request_id: str,
    role: str | None,
    model: str,
    canonical_slugs: list[str],
    entry_count: int,
    injected_bytes: int,
    budget_bytes: int,
) -> Event:
    """Inline-fallback success — resolved SKILL bodies inlined into system prompt.

    Contract stub for ``task:stage2-fallback-standardization``; emit sites owned
    by Stage-2. See ``todo:skills-delivery-telemetry-async-path``.
    """
    return Event(
        signal="dispatch.skills.inline.injected",
        payload={
            "request_id": request_id,
            "role": role,
            "model": model,
            "canonical_slugs": canonical_slugs,
            "entry_count": entry_count,
            "injected_bytes": injected_bytes,
            "budget_bytes": budget_bytes,
        },
        scope="node",
    )


@event_factory
def DispatchSkillsInlineRejected(  # noqa: N802
    request_id: str,
    role: str | None,
    model: str,
    skills: list[str],
    budget_bytes: int,
    reason_code: str = "budget",
) -> Event:
    """Caller-origin Layer-C inline injection rejected at budget admission."""
    return Event(
        signal="dispatch.skills.inline.rejected",
        payload={
            "request_id": request_id,
            "role": role,
            "model": model,
            "skills": skills,
            "budget_bytes": budget_bytes,
            "reason_code": reason_code,
        },
        scope="node",
    )


@event_factory
def DispatchSkillsInlineOverflow(  # noqa: N802
    request_id: str,
    role: str | None,
    model: str,
    canonical_slugs: list[str],
    requested_bytes: int,
    budget_bytes: int,
) -> Event:
    """Inline-fallback hard-error when injection exceeds the token/byte budget.

    Contract stub for ``task:stage2-fallback-standardization``; emit sites owned
    by Stage-2. See ``todo:skills-delivery-telemetry-async-path``.
    """
    return Event(
        signal="dispatch.skills.inline.overflow",
        payload={
            "request_id": request_id,
            "role": role,
            "model": model,
            "canonical_slugs": canonical_slugs,
            "requested_bytes": requested_bytes,
            "budget_bytes": budget_bytes,
        },
        scope="node",
    )


@event_factory
def DispatchSkillsPredicatedSkipped(  # noqa: N802
    request_id: str,
    role: str | None,
    model: str,
    skills: list[str],
) -> Event:
    """Scope-default MCP-predicated skills skipped on a non-MCP dispatch."""
    return Event(
        signal="dispatch.skills.predicated.skipped",
        payload={
            "request_id": request_id,
            "role": role,
            "model": model,
            "skills": skills,
        },
        scope="node",
    )


@event_factory
def DispatchSkillsInlineResolved(  # noqa: N802
    request_id: str,
    seat: str,
    model: str | None,
    injected: list[dict[str, object]],
    dropped: list[dict[str, object]],
    total_bytes: int,
    budget_bytes: int,
    cache_hit: bool,
    cold_fetches: int,
    elapsed_ms: int,
    deadline_hit: bool,
) -> Event:
    """Inline-only dispatch body injection audit (G3-owned, not B3 substrate)."""
    return Event(
        signal="dispatch.skills.inline.resolved",
        payload={
            "request_id": request_id,
            "seat": seat,
            "model": model,
            "injected": injected,
            "dropped": dropped,
            "total_bytes": total_bytes,
            "budget_bytes": budget_bytes,
            "cache_hit": cache_hit,
            "cold_fetches": cold_fetches,
            "elapsed_ms": elapsed_ms,
            "deadline_hit": deadline_hit,
        },
        scope="node",
    )


@event_factory
def FrontierHandoffDeprecatedAlias(  # noqa: N802
    request_id: str,
    normalized_op: str,
    seat: str,
) -> Event:
    """Deprecated op=handoff,seat=cursor-sdk normalized to the generate path."""
    return Event(
        signal="frontier.handoff.deprecated.alias",
        payload={
            "request_id": request_id,
            "normalized_op": normalized_op,
            "seat": seat,
        },
        scope="node",
    )


@event_factory
def FrontierDensifyReviewAdmitted(  # noqa: N802
    parent_request_id: str,
    parent_execution_id: str | None,
    parent_dispatch_thread_id: str,
    densify_thread_id: str,
    staged_draft_uri: str,
    reasoning_trace_uri: str,
    density_triage: str | None,
    draft_adequacy: str,
    opt_out: bool,
    opt_out_reason_code: str | None,
    reviewer_family: str | None,
    reviewer_model: str | None,
    target_thread_id: str | None,
    review_execution_id: str | None,
    review_spawned: bool,
    hold_reason: str | None = None,
) -> Event:
    """Default-on densify candidate admitted, opted-out, or blank-held."""
    return Event(
        signal="frontier.densify.review.admitted",
        payload={
            "parent_request_id": parent_request_id,
            "parent_execution_id": parent_execution_id,
            "parent_dispatch_thread_id": parent_dispatch_thread_id,
            "densify_thread_id": densify_thread_id,
            "staged_draft_uri": staged_draft_uri,
            "reasoning_trace_uri": reasoning_trace_uri,
            "density_triage": density_triage,
            "draft_adequacy": draft_adequacy,
            "opt_out": opt_out,
            "opt_out_reason_code": opt_out_reason_code,
            "reviewer_family": reviewer_family,
            "reviewer_model": reviewer_model,
            "target_thread_id": target_thread_id,
            "review_execution_id": review_execution_id,
            "auto_review_child": False,
            "review_spawned": review_spawned,
            "hold_reason": hold_reason,
        },
        scope="node",
    )


@event_factory
def FrontierDensifyReviewOutcome(  # noqa: N802
    parent_request_id: str,
    review_execution_id: str,
    finding_delta: int,
    reviewer_concur_only: bool,
    folded_finding_ids: list[str],
) -> Event:
    """Validated densify_review_reconcile closeout on the densify thread."""
    return Event(
        signal="frontier.densify.review.outcome",
        payload={
            "parent_request_id": parent_request_id,
            "review_execution_id": review_execution_id,
            "finding_delta": finding_delta,
            "reviewer_concur_only": reviewer_concur_only,
            "folded_finding_ids": folded_finding_ids,
        },
        scope="node",
    )


@event_factory
def FrontierSkillSuggestDispatchCompleted(  # noqa: N802
    request_id: str,
    agent: str,
    route: str,
    latency_ms: int,
) -> Event:
    """Skill-suggest dispatch returned via worker-hop capture path."""
    return Event(
        signal="frontier.skill_suggest_dispatch.completed",
        payload={
            "request_id": request_id,
            "agent": agent,
            "route": route,
            "latency_ms": latency_ms,
        },
        scope="node",
    )


@event_factory
def FrontierSdkCostRiskWarning(  # noqa: N802
    request_id: str | None,
    execution_id: str | None,
    model_id: str,
    contract: str,
    suppressed: bool,
    suppression_reason: str | None = None,
    cost_intent_reason: str | None = None,
    suggested_knobs: dict[str, str] | None = None,
    suggested_model: str | None = None,
) -> Event:
    """Cost-risk alignment warning emitted for mechanical opus/sonnet dispatches."""
    payload: dict[str, object] = {
        "model_id": model_id,
        "contract": contract,
        "suppressed": suppressed,
    }
    if request_id is not None:
        payload["request_id"] = request_id
    if execution_id is not None:
        payload["execution_id"] = execution_id
    if suppression_reason is not None:
        payload["suppression_reason"] = suppression_reason
    if cost_intent_reason is not None:
        payload["cost_intent_reason"] = cost_intent_reason
    if suggested_knobs is not None:
        payload["suggested_knobs"] = suggested_knobs
    if suggested_model is not None:
        payload["suggested_model"] = suggested_model
    return Event(
        signal="frontier.sdk.cost_risk.warning",
        payload=payload,
        scope="node",
    )


@event_factory
def FrontierSdkKnobDropped(  # noqa: N802
    model_id: str,
    knob: str,
    requested: str,
    reason: Literal["unsupported", "invalid_value"],
) -> Event:
    """Unsupported or invalid cursor-sdk knob dropped at alignment."""
    return Event(
        signal="frontier.sdk.knob.dropped",
        payload={
            "model_id": model_id,
            "knob": knob,
            "requested": requested,
            "reason": reason,
        },
        scope="node",
    )


@event_factory
def FrontierSdkReasoningEffortRejected(  # noqa: N802
    model_id: str,
    requested: str,
) -> Event:
    """Wrong-surface ``reasoning_effort`` rejected at cursor-sdk prepare (BIND_B)."""
    return Event(
        signal="frontier.sdk.reasoning_effort.rejected",
        payload={
            "model_id": model_id,
            "requested": requested,
        },
        scope="node",
    )


@event_factory
def DispatchCapabilityCardMissing(  # noqa: N802
    request_id: str,
    model: str,
    capability_field: str,
    reason_code: str,
) -> Event:
    """Dispatch admission rejected: model capability card missing or incomplete."""
    return Event(
        signal="dispatch.capability.card.missing",
        payload={
            "request_id": request_id,
            "model": model,
            "capability_field": capability_field,
            "reason_code": reason_code,
        },
        scope="node",
    )


@event_factory
def FrontierReviewChildPromptBind(  # noqa: N802
    *,
    parent_execution_id: str,
    child_execution_id: str | None,
    delivery_thread_id: str,
    prompt_bind_mode: str,
    prompt_turn_number: int | None,
    latest_read_outcome: str,
    bound_prompt_class: str,
    bound_prompt_digest: str,
) -> Event:
    """Spawn-hook prompt bind instrumentation — Path-1 vs Path-2 discriminator (6655)."""
    return Event(
        signal="frontier.review_child.prompt_bind",
        payload={
            "parent_execution_id": parent_execution_id,
            "child_execution_id": child_execution_id,
            "delivery_thread_id": delivery_thread_id,
            "prompt_bind_mode": prompt_bind_mode,
            "prompt_turn_number": prompt_turn_number,
            "latest_read_outcome": latest_read_outcome,
            "bound_prompt_class": bound_prompt_class,
            "bound_prompt_digest": bound_prompt_digest,
        },
        scope="node",
    )


@event_factory
def FrontierAdmitPointerLoopClosure(  # noqa: N802
    *,
    request_id: str | None,
    execution_id: str | None,
    admit_target_thread: str,
    prompt_source_thread: str,
    prompt_bind_mode: str | None,
    prompt_turn_number: int | None,
    has_explicit_prompt_source: bool,
    loop_closure: bool,
    allowlisted_silent: bool,
    would_have_refused: bool,
    would_have_refused_total: int,
    reason: str,
    spawn_uses_latest_on_thread: bool,
    refused: bool = False,
) -> Event:
    """B.3 admission refuse — joinable with review_child.prompt_bind (6655)."""
    return Event(
        signal="frontier.admit_pointer.loop_closure",
        payload={
            "request_id": request_id,
            "execution_id": execution_id,
            "admit_target_thread": admit_target_thread,
            "prompt_source_thread": prompt_source_thread,
            "prompt_bind_mode": prompt_bind_mode,
            "prompt_turn_number": prompt_turn_number,
            "has_explicit_prompt_source": has_explicit_prompt_source,
            "loop_closure": loop_closure,
            "allowlisted_silent": allowlisted_silent,
            "would_have_refused": would_have_refused,
            "would_have_refused_total": would_have_refused_total,
            "reason": reason,
            "spawn_uses_latest_on_thread": spawn_uses_latest_on_thread,
            "refused": refused,
        },
        scope="node",
    )


@event_factory
def FrontierSdkReviewChildSpawned(  # noqa: N802
    execution_id: str,
    parent_execution_id: str,
    parent_thread_id: str,
    reviewer_model: str,
    reviewer_family: str,
    dedupe_key: str,
) -> Event:
    """Auto review child spawned after generate/cursor-sdk worker completion."""
    return Event(
        signal="frontier.sdk.review_child.spawned",
        payload={
            "execution_id": execution_id,
            "parent_execution_id": parent_execution_id,
            "parent_thread_id": parent_thread_id,
            "reviewer_model": reviewer_model,
            "reviewer_family": reviewer_family,
            "dedupe_key": dedupe_key,
        },
        scope="node",
    )


@event_factory
def FrontierReviewChildContextMissing(  # noqa: N802
    execution_id: str,
    thread_id: str,
    attempts: int,
) -> Event:
    """Admission context miss exhausted reconcile window — fail closed, no spawn."""
    return Event(
        signal="frontier.review_child.context_missing",
        payload={
            "execution_id": execution_id,
            "thread_id": thread_id,
            "attempts": attempts,
        },
        scope="node",
    )


@event_factory
def PipelineFrontierDispatchCorpusInlined(  # noqa: N802
    request_id: str,
    role: str | None,
    model: str,
    injected_count: int,
    dropped_count: int,
    injected_bytes: int,
    dropped_bytes: int,
    budget_bytes: int,
) -> Event:
    """Corpus document bodies inlined for an inline-only dispatch."""
    return Event(
        signal="pipeline.frontier.dispatch.corpus.inlined",
        payload={
            "request_id": request_id,
            "role": role,
            "model": model,
            "injected_count": injected_count,
            "dropped_count": dropped_count,
            "injected_bytes": injected_bytes,
            "dropped_bytes": dropped_bytes,
            "budget_bytes": budget_bytes,
        },
        scope="node",
    )


@event_factory
def PipelineFrontierDispatchCorpusUnresolved(  # noqa: N802
    request_id: str,
    role: str | None,
    model: str,
    uri: str,
) -> Event:
    """A ``<corpus>`` URI could not be resolved (soft-drop)."""
    return Event(
        signal="pipeline.frontier.dispatch.corpus.unresolved",
        payload={
            "request_id": request_id,
            "role": role,
            "model": model,
            "uri": uri,
        },
        scope="node",
    )


@event_factory
def FrontierSkillSuggestDispatchDegraded(  # noqa: N802
    request_id: str,
    agent: str,
    route: str,
    reason: str,
    latency_ms: int,
    execution_id: str | None = None,
    thread_id: str | None = None,
    dispatch_id: str | None = None,
    last_worker_status: str | None = None,
    last_heartbeat_at: str | None = None,
) -> Event:
    """Skill-suggest dispatch fell back to direct POST /skills/suggest."""
    payload: dict[str, object] = {
        "request_id": request_id,
        "agent": agent,
        "route": route,
        "reason": reason,
        "latency_ms": latency_ms,
    }
    if execution_id is not None:
        payload["execution_id"] = execution_id
    if thread_id is not None:
        payload["thread_id"] = thread_id
    if dispatch_id is not None:
        payload["dispatch_id"] = dispatch_id
    if last_worker_status is not None:
        payload["last_worker_status"] = last_worker_status
    if last_heartbeat_at is not None:
        payload["last_heartbeat_at"] = last_heartbeat_at
    return Event(
        signal="frontier.skill_suggest_dispatch.degraded",
        payload=payload,
        scope="node",
    )
