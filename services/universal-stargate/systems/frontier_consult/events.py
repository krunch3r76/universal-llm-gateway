"""Endpoint-level signals for team/frontier generate routes."""

from __future__ import annotations

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
) -> Event:
    """SDK worker dispatch accepted."""
    return Event(
        signal="frontier.sdk.worker.dispatched",
        payload={
            "request_id": request_id,
            "thread_id": thread_id,
            "execution_id": execution_id,
        },
        scope="node",
    )


@event_factory
def FrontierSdkWorkerDispatchFailed(  # noqa: N802
    request_id: str,
    thread_id: str,
    execution_id: str,
    error: str,
) -> Event:
    """SDK worker dispatch rejected or unreachable."""
    return Event(
        signal="frontier.sdk.worker.failed",
        payload={
            "request_id": request_id,
            "thread_id": thread_id,
            "execution_id": execution_id,
            "error": error,
        },
        scope="node",
    )


@event_factory
def FrontierHandoffPacketEnriched(  # noqa: N802
    request_id: str,
    packet_path: str,
    to_agent: str,
    skills_added: list[str],
    threads_added: list[str],
) -> Event:
    """Web handoff packet auto-enriched before validation (assertion #19650)."""
    return Event(
        signal="frontier.handoff.packet.enriched",
        payload={
            "request_id": request_id,
            "packet_path": packet_path,
            "to_agent": to_agent,
            "skills_added": skills_added,
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
def InlineBodyInjectionResolved(  # noqa: N802
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
        signal="inline.body.injection.resolved",
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
def FrontierSkillSuggestDispatchDegraded(  # noqa: N802
    request_id: str,
    agent: str,
    route: str,
    reason: str,
    latency_ms: int,
) -> Event:
    """Skill-suggest dispatch fell back to direct POST /skills/suggest."""
    return Event(
        signal="frontier.skill_suggest_dispatch.degraded",
        payload={
            "request_id": request_id,
            "agent": agent,
            "route": route,
            "reason": reason,
            "latency_ms": latency_ms,
        },
        scope="node",
    )
