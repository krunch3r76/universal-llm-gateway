"""Async pipeline dispatch lifecycle events.

Node-scoped signals emitted by the ``PipelineExecutionTracker`` to mark the
async-transport boundary. These complement the existing global
``pipeline.started`` / ``pipeline.completed`` / ``pipeline.failed`` signals:
``dispatch.*`` describes the async surface (admission, terminal tracker state,
capacity rejection) while the pipeline signals describe the DAG lifecycle.

Invariant: ∀ async dispatch accepted ⟹ emit ``pipeline.dispatch.async``.
Invariant: ∀ tracker terminal transition ⟹ emit ``pipeline.dispatch.completed``.
Invariant: ∀ admission refused ⟹ emit ``pipeline.dispatch.rejected``.
Invariant: ∀ terminal record TTL-pruned ⟹ emit ``pipeline.dispatch.tracker.expired``.
Invariant: ∀ journal write/read/prune ⟹ emit ``pipeline.dispatch.journal.*``.
Invariant: ∀ frontier_dispatch_v1 step execution
⟹ emit ``pipeline.frontier.dispatch.*``.
"""

from __future__ import annotations

from universal_event_bus import Event, event_factory


@event_factory
def PipelineDispatchAsync(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    has_delivery_hook: bool,
    caller_agent: str | None = None,
) -> Event:
    """Emitted when the async tracker admits a new execution.

    Payload:
        pipeline_id: Pipeline identifier
        execution_id: Newly minted execution UUID
        has_delivery_hook: Whether a ``result_delivery`` config was supplied
    """
    return Event(
        signal="pipeline.dispatch.async",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "has_delivery_hook": has_delivery_hook,
            "caller_agent": caller_agent,
        },
    )


@event_factory
def PipelineDispatchCompleted(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    status: str,
    duration_s: float,
    caller_agent: str | None = None,
) -> Event:
    """Emitted when the async tracker records a terminal state.

    Payload:
        pipeline_id: Pipeline identifier
        execution_id: Execution UUID
        status: Terminal status (``completed`` or ``failed``)
        duration_s: Wall-clock seconds from admission to terminal transition
    """
    return Event(
        signal="pipeline.dispatch.completed",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "status": status,
            "duration_s": duration_s,
            "caller_agent": caller_agent,
        },
    )


@event_factory
def PipelineDispatchCancelled(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    source: str,
) -> Event:
    """Emitted when a running dispatch is cancelled by an explicit DELETE."""
    return Event(
        signal="pipeline.dispatch.cancelled",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "source": source,
        },
    )


@event_factory
def PipelineDispatchRejected(  # noqa: N802
    pipeline_id: str,
    reason: str,
) -> Event:
    """Emitted when the async tracker refuses to admit a new execution.

    Payload:
        pipeline_id: Requested pipeline identifier
        reason: Rejection reason (e.g. ``capacity_exhausted``)
    """
    return Event(
        signal="pipeline.dispatch.rejected",
        payload={
            "pipeline_id": pipeline_id,
            "reason": reason,
        },
    )


@event_factory
def PipelineDispatchTrackerExpired(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    status: str,
    age_seconds: float,
) -> Event:
    """Emitted when a terminal tracker record is pruned by TTL.

    Gives observability on whether the retention window is long enough in
    practice: if a caller never polled before the record expired, the result
    is gone. Tracking the frequency of these events informs whether to bump
    retention further or move to persistent storage (phase 2+).

    Payload:
        pipeline_id: Pipeline identifier
        execution_id: Execution UUID being dropped
        status: Terminal status the record held (``completed`` or ``failed``)
        age_seconds: Seconds elapsed since terminal transition
    """
    return Event(
        signal="pipeline.dispatch.tracker.expired",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "status": status,
            "age_seconds": age_seconds,
        },
    )


@event_factory
def PipelineDispatchJournalWritten(  # noqa: N802
    execution_id: str,
    status: str,
    bytes_written: int,
) -> Event:
    """Emitted when a terminal dispatch record is persisted to sqlite."""
    return Event(
        signal="pipeline.dispatch.journal.written",
        payload={
            "execution_id": execution_id,
            "status": status,
            "bytes": bytes_written,
        },
        scope="node",
    )


@event_factory
def PipelineDispatchJournalRead(  # noqa: N802
    execution_id: str,
    age_seconds: float,
) -> Event:
    """Emitted when tracker fallback serves a terminal record from sqlite."""
    return Event(
        signal="pipeline.dispatch.journal.read",
        payload={
            "execution_id": execution_id,
            "age_seconds": age_seconds,
        },
        scope="node",
    )


@event_factory
def PipelineDispatchJournalPruned(  # noqa: N802
    records_deleted: int,
    oldest_deleted_age_seconds: float | None,
) -> Event:
    """Emitted once per prune round for dispatch journal retention."""
    return Event(
        signal="pipeline.dispatch.journal.pruned",
        payload={
            "records_deleted": records_deleted,
            "oldest_deleted_age_seconds": oldest_deleted_age_seconds,
        },
        scope="node",
    )


@event_factory
def PipelineFrontierDispatchHydrated(  # noqa: N802
    agent: str,
    execution_id: str,
    briefing_bytes: int,
    section_counts: dict[str, int],
    continuation_id: str | None,
) -> Event:
    """Emitted after a ``frontier_dispatch_v1`` step hydrates its Cortex boot.

    Per-dispatch telemetry only — not re-emitted on master. Only fires when a
    persona is specified (persona-free dispatches skip hydration entirely).

    Payload:
        agent: Dispatched-agent identity (``orion``, ``oppie``, ``bard``, ``web``)
        execution_id: Pipeline execution UUID (joins with dispatch.* signals)
        briefing_bytes: Size of the rendered briefing card in characters
        section_counts: Per-section item counts (sessions, deadlines, todos, ...)
        continuation_id: Transcript entity_id when a continuation was resolved
    """
    return Event(
        signal="pipeline.frontier.dispatch.hydrated",
        payload={
            "agent": agent,
            "execution_id": execution_id,
            "briefing_bytes": briefing_bytes,
            "section_counts": section_counts,
            "continuation_id": continuation_id,
        },
        scope="node",
    )


@event_factory
def PipelineFrontierDispatchToolCalled(  # noqa: N802
    agent: str | None,
    execution_id: str,
    tool_name: str,
    turn: int,
    elapsed_ms: float,
    provider: str,
) -> Event:
    """Emitted per successful tool call inside a ``frontier_dispatch_v1`` loop.

    Payload:
        agent: Persona identity if set, else ``None`` for persona-free dispatches
        tool_name: Dispatched tool (``cortex``, ``agent_bus``, ``rag_search``, ...)
        turn: 1-indexed tool-loop turn the call occurred in
        elapsed_ms: Wall-clock duration of the tool call
        provider: Effective provider (``anthropic``, ``openai``, ``xai``, ``google``)
    """
    return Event(
        signal="pipeline.frontier.dispatch.tool.called",
        payload={
            "agent": agent,
            "execution_id": execution_id,
            "tool_name": tool_name,
            "turn": turn,
            "elapsed_ms": elapsed_ms,
            "provider": provider,
        },
        scope="node",
    )


@event_factory
def PipelineFrontierDispatchToolFailed(  # noqa: N802
    agent: str | None,
    execution_id: str,
    tool_name: str,
    turn: int,
    elapsed_ms: float,
    error: str,
    provider: str,
) -> Event:
    """Emitted when a tool call returns an error envelope or raises."""
    return Event(
        signal="pipeline.frontier.dispatch.tool.failed",
        payload={
            "agent": agent,
            "execution_id": execution_id,
            "tool_name": tool_name,
            "turn": turn,
            "elapsed_ms": elapsed_ms,
            "error": error,
            "provider": provider,
        },
        scope="node",
    )


@event_factory
def PipelineFrontierDispatchCompleted(  # noqa: N802
    agent: str | None,
    execution_id: str,
    turns_used: int,
    tool_calls_made: int,
    reasoning_present: bool,
    prompt_tokens: int,
    completion_tokens: int,
    provider: str,
) -> Event:
    """Emitted when the native tool loop returns final content.

    Payload:
        agent: Persona identity if set, else ``None`` for persona-free dispatches
        turns_used: Number of model+tool rounds consumed (≥1)
        tool_calls_made: Total tool invocations across all turns
        reasoning_present: Whether the model surfaced reasoning trace text
        provider: Effective provider (``anthropic``, ``openai``, ``xai``, ``google``)
    """
    return Event(
        signal="pipeline.frontier.dispatch.completed",
        payload={
            "agent": agent,
            "execution_id": execution_id,
            "turns_used": turns_used,
            "tool_calls_made": tool_calls_made,
            "reasoning_present": reasoning_present,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "provider": provider,
        },
        scope="node",
    )


@event_factory
def PipelineFrontierDispatchRemoteMcpEnabled(  # noqa: N802
    execution_id: str,
    agent: str | None,
    model: str,
    provider: str,
) -> Event:
    """Emitted once per ``frontier_dispatch_v1`` execution with ``remote_mcp=True``.

    Fires before the native call, immediately after the adapter decides to
    attach the provider-native MCP descriptor. Implies client-side tool loop
    is disabled for this execution (``mcp_tool_loop=False``).

    Payload:
        execution_id: Pipeline execution UUID
        agent: Persona identity if set, else ``None`` for persona-free
        model: Raw model string as supplied by the caller
        provider: Effective provider (``anthropic``, ``openai``, ``xai``)
    """
    return Event(
        signal="pipeline.frontier.dispatch.remotemcp.enabled",
        payload={
            "execution_id": execution_id,
            "agent": agent,
            "model": model,
            "provider": provider,
        },
        scope="node",
    )


@event_factory
def PipelineFrontierDispatchRemoteMcpMisconfigured(  # noqa: N802
    execution_id: str,
    agent: str | None,
    model: str,
    reason: str,
) -> Event:
    """Emitted when remote-MCP env resolution fails at build time.

    Precedes the terminal ``pipeline_execution_failed`` — the caller can
    distinguish a structural misconfiguration (missing ``MCP_PUBLIC_URL`` or
    ``MCP_AUTH_TOKEN`` in the Stargate container env) from an upstream
    provider or tool-loop error by looking for this signal first.

    Payload:
        execution_id: Pipeline execution UUID
        agent: Persona identity if set, else ``None``
        model: Raw model string as supplied by the caller
        reason: Human-readable ``RuntimeError`` message from ``resolve_mcp_env``
    """
    return Event(
        signal="pipeline.frontier.dispatch.remotemcp.misconfigured",
        payload={
            "execution_id": execution_id,
            "agent": agent,
            "model": model,
            "reason": reason,
        },
        scope="node",
    )


@event_factory
def PipelineFrontierDispatchRemoteMcpUnsupported(  # noqa: N802
    execution_id: str,
    agent: str | None,
    model: str,
    provider: str,
    requested: bool,
    reason: str,
) -> Event:
    """Emitted when the step handler rejects a ``remote_mcp`` request that
    conflicts with the resolved provider's capability matrix.

    Precedes the terminal ``pipeline_execution_failed`` carrying error
    ``code=remote_mcp_unsupported``. Distinguishes a structural
    provider/capability mismatch (caller asked for a mode the provider
    cannot fulfil) from ``remotemcp.misconfigured`` (env resolution failure)
    and from upstream provider errors.

    Payload:
        execution_id: Pipeline execution UUID
        agent: Persona identity if set, else ``None``
        model: Raw model string as supplied by the caller
        provider: Effective provider (``anthropic``, ``openai``, ``xai``, ``google``)
        requested: Value the caller asked for (True or False)
        reason: Human-readable explanation of the capability violation
    """
    return Event(
        signal="pipeline.frontier.dispatch.remotemcp.unsupported",
        payload={
            "execution_id": execution_id,
            "agent": agent,
            "model": model,
            "provider": provider,
            "requested": requested,
            "reason": reason,
        },
        scope="node",
    )


@event_factory
def PipelineFrontierDispatchStarted(  # noqa: N802
    execution_id: str,
    agent: str | None,
    model: str,
    provider: str,
    boot_level: str,
    remote_mcp: bool,
) -> Event:
    """Emitted when a ``frontier_dispatch_v1`` execution begins its native call.

    Fires once per step execution, after hydration (for team-seat dispatches)
    and before ``run_native_tool_loop``. The ``.started`` / ``.completed`` /
    ``.exhausted`` triple gives ``_provider_health`` per-provider call and
    completion rates previously derived from
    ``mcp.frontier.generate.called`` / ``mcp.frontier.generate.completed``.

    Payload:
        execution_id: Pipeline execution UUID
        agent: Persona identity if set, else ``None`` for persona-free dispatches
        model: Raw model string as supplied by the caller
        provider: Effective provider (``anthropic``, ``openai``, ``xai``, ``google``)
        boot_level: ``team`` (persona dispatch) or ``none`` (persona-free)
        remote_mcp: True iff adapter-level remote MCP injection is active
    """
    return Event(
        signal="pipeline.frontier.dispatch.started",
        payload={
            "execution_id": execution_id,
            "agent": agent,
            "model": model,
            "provider": provider,
            "boot_level": boot_level,
            "remote_mcp": remote_mcp,
        },
        scope="node",
    )


@event_factory
def PipelineFrontierDispatchOutputShort(  # noqa: N802
    agent: str | None,
    execution_id: str,
    model: str,
    provider: str,
    boot_level: str,
    output_tokens: int,
    tool_calls_made: int,
    finish_reason: str | None,
    block_reason: str | None,
    content_preview: str,
) -> Event:
    """Emitted when a team/full dispatch returns ``output_tokens < 500``.

    Replaces the deprecated ``mcp.frontier.output.short`` signal: the same
    anomaly is now observable for every ``frontier_dispatch_v1`` caller
    (MCP sugar, pipeline callers, future HTTP surfaces) instead of only
    callers entering through ``frontier_generate``. Gate is ``boot_level ∈
    {team, full}`` plus the short-output threshold — enforced inside
    ``detect_output_short``. The handler passes ``boot_level="team"`` for
    persona dispatches and ``"none"`` otherwise; the detector filters the
    latter.

    Payload:
        agent: Persona identity (gate: only emitted when set)
        model: Raw model string as supplied by the caller
        provider: Effective provider (``anthropic``, ``openai``, ``xai``, ``google``)
        boot_level: Boot tier at dispatch time (``team`` or ``full``)
        output_tokens: Final completion token count from the adapter usage
        tool_calls_made: Total tool invocations across all turns
        finish_reason: Provider-native finish reason, if any
        block_reason: Provider-native block reason, if any
        content_preview: First ~500 chars of content, for triage
    """
    return Event(
        signal="pipeline.frontier.dispatch.output.short",
        payload={
            "agent": agent,
            "execution_id": execution_id,
            "model": model,
            "provider": provider,
            "boot_level": boot_level,
            "output_tokens": output_tokens,
            "tool_calls_made": tool_calls_made,
            "finish_reason": finish_reason,
            "block_reason": block_reason,
            "content_preview": content_preview,
        },
        scope="node",
    )


@event_factory
def PipelineFrontierDispatchTerminationShadow(  # noqa: N802
    agent: str | None,
    execution_id: str,
    model: str,
    provider: str,
    boot_level: str,
    output_tokens: int,
    finish_reason: str | None,
    block_reason: str | None,
    reason: str,
    confidence: float,
    evidence: list[dict[str, object]],
    suggested_next_action: str,
    trace_visibility: str,
    generate_id: str,
    detector: dict[str, str],
) -> Event:
    """Emitted when the Gemini thought trace looks like a silent termination.

    Replaces the deprecated ``mcp.frontier.thought.termination.shadow``
    signal. v1 scope: provider ``google`` + ``boot_level ∈ {team, full}``;
    the detector itself enforces both gates.

    Payload:
        reason: ``refusal`` | ``incapacity`` | ``policy`` | ``scope`` |
            ``token_exhaustion`` | ``loop``
        confidence: [0, 1] scalar, multi-evidence boost applied
        evidence: list of ``{kind, score, excerpt}`` dicts
        suggested_next_action: ``escalate_to_user`` | ``switch_model`` |
            ``retry_with_context`` | ``none``
        trace_visibility: ``partial`` (Gemini exposes thought summaries only)
        generate_id: UUID minted at detection site for cross-event correlation
        detector: ``{mode, version, provider, adapter}`` descriptor
    """
    return Event(
        signal="pipeline.frontier.dispatch.termination.shadow",
        payload={
            "agent": agent,
            "execution_id": execution_id,
            "model": model,
            "provider": provider,
            "boot_level": boot_level,
            "output_tokens": output_tokens,
            "finish_reason": finish_reason,
            "block_reason": block_reason,
            "reason": reason,
            "confidence": confidence,
            "evidence": evidence,
            "suggested_next_action": suggested_next_action,
            "trace_visibility": trace_visibility,
            "generate_id": generate_id,
            "detector": detector,
        },
        scope="node",
    )


@event_factory
def PipelineFrontierDispatchAgentModelMismatch(  # noqa: N802
    execution_id: str,
    agent: str,
    requested_model: str,
    valid_family: list[str],
) -> Event:
    """Emitted when the dispatch handler rejects an agent + model combination
    whose provider does not match the agent's identity-bound provider family.

    Precedes the terminal ``pipeline_execution_failed``. Distinguishes a
    caller misconfiguration (wrong provider for the agent) from MCP
    misconfiguration or upstream provider errors.

    Payload:
        execution_id: Pipeline execution UUID
        agent: Agent slug that was specified (``orion``, ``oppie``, etc.)
        requested_model: Model string the caller supplied
        valid_family: Allowed model identifiers for this agent
    """
    return Event(
        signal="pipeline.frontier.dispatch.agent.model.mismatch",
        payload={
            "execution_id": execution_id,
            "agent": agent,
            "requested_model": requested_model,
            "valid_family": valid_family,
        },
        scope="node",
    )


@event_factory
def PipelineFrontierDispatchExhausted(  # noqa: N802
    agent: str | None,
    execution_id: str,
    turns_used: int,
    tool_calls_made: int,
    provider: str,
) -> Event:
    """Emitted when the tool loop hits ``max_tool_turns`` without terminal content.

    Signals a misbehaving dispatch: the model kept requesting tools past the
    configured budget. Content returned to the caller may be empty or the
    last assistant message that still included tool_calls.
    """
    return Event(
        signal="pipeline.frontier.dispatch.exhausted",
        payload={
            "agent": agent,
            "execution_id": execution_id,
            "turns_used": turns_used,
            "tool_calls_made": tool_calls_made,
            "provider": provider,
        },
        scope="node",
    )
