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
    op: str = "",
    output_contract: str = "inline",
) -> Event:
    """Emitted when the async tracker admits a new execution.

    Payload:
        pipeline_id: Pipeline identifier
        execution_id: Newly minted execution UUID
        has_delivery_hook: Whether a delivery config was supplied
        op: Dispatch op (``generate`` | ``to_thread`` | empty for legacy)
        output_contract: Where the work product lands (``inline`` | ``thread``)
    """
    return Event(
        signal="pipeline.dispatch.async",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "has_delivery_hook": has_delivery_hook,
            "caller_agent": caller_agent,
            "op": op,
            "output_contract": output_contract,
        },
    )


@event_factory
def PipelineDispatchCompleted(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    status: str,
    duration_s: float,
    caller_agent: str | None = None,
    op: str = "",
    output_contract: str = "inline",
) -> Event:
    """Emitted when the async tracker records a terminal state.

    Payload:
        pipeline_id: Pipeline identifier
        execution_id: Execution UUID
        status: Terminal status (``completed`` or ``failed``)
        duration_s: Wall-clock seconds from admission to terminal transition
        op: Dispatch op (``generate`` | ``to_thread`` | empty for legacy)
        output_contract: Where the work product lands (``inline`` | ``thread``)
    """
    return Event(
        signal="pipeline.dispatch.completed",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "status": status,
            "duration_s": duration_s,
            "caller_agent": caller_agent,
            "op": op,
            "output_contract": output_contract,
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
def PipelineFrontierDispatchToolRequested(  # noqa: N802
    agent: str | None,
    execution_id: str,
    tool_name: str,
    provider: str,
    tool_call_id: str | None = None,
) -> Event:
    """Emitted when the model begins generating a tool_use block.

    Fires at the streaming event that announces a tool call — BEFORE the tool
    executes. Distinct from ``pipeline.frontier.dispatch.tool.called`` which
    emits after execution.

    ``tool_call_id`` correlates this event with the subsequent
    ``pipeline.frontier.dispatch.tool.called`` / ``...tool.failed`` events that
    carry the same id from the native-loop result.  Anthropic exposes it as
    ``content_block.id``; OpenAI/xAI expose it as ``item.id``;
    Google has no native id and emits ``None``.
    """
    return Event(
        signal="pipeline.frontier.dispatch.tool.requested",
        payload={
            "agent": agent,
            "execution_id": execution_id,
            "tool_name": tool_name,
            "provider": provider,
            "tool_call_id": tool_call_id,
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
        tool_name: Dispatched tool (``cortex``, ``agent_bus``, ``rag``, ...)
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
    arguments: dict | None = None,
    full_error: dict | None = None,
    retry_count: int = 0,
) -> Event:
    """Emitted when a tool call returns an error envelope or raises.

    Enhanced payload gives much better observability into tool failures
    and retry behavior. Do not retry the exact same (tool_name, arguments)
    combination more than once per turn.
    """
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
            "arguments": arguments,
            "full_error": full_error,
            "retry_count": retry_count,
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
    op: str = "",
    finish_reason: str | None = None,
    block_reason: str | None = None,
) -> Event:
    """Emitted when the native tool loop returns final content.

    Payload:
        agent: Persona identity if set, else ``None`` for persona-free dispatches
        turns_used: Number of model+tool rounds consumed (≥1)
        tool_calls_made: Total tool invocations across all turns
        reasoning_present: Whether the model surfaced reasoning trace text
        provider: Effective provider (``anthropic``, ``openai``, ``xai``, ``google``)
        op: Dispatch op (``generate`` | ``to_thread`` | empty for legacy)
        finish_reason: Provider-native finish reason from the final response
            (``stop`` | ``tool_calls`` | ``length`` | ``end_turn`` | None).
            Surfaced so callers can distinguish a clean stop from a ceiling-hit
            (``tool_calls`` or ``length`` with empty content) without parsing
            the raw envelope.
        block_reason: Provider-native block reason, if any (Google safety stops).
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
            "op": op,
            "finish_reason": finish_reason,
            "block_reason": block_reason,
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
    op: str = "",
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
        boot_level: Internal observability vocabulary derived from agent
            presence at dispatch: ``team`` (persona dispatch) or ``none``
            (persona-free). NOT a caller-supplied parameter; the public MCP
            surface is ``team_dispatch`` (persona) plus ``frontier_dispatch``
            (raw) with no ``boot`` field.
        remote_mcp: True iff adapter-level remote MCP injection is active
        op: Dispatch op (``generate`` | ``to_thread`` | empty for legacy)
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
            "op": op,
        },
        scope="node",
    )


@event_factory
def PipelineFrontierDispatchEmptyCompletion(  # noqa: N802
    execution_id: str,
    agent: str | None,
    model: str,
    provider: str,
    turns_used: int,
    tool_calls_made: int,
    finish_reason: str | None,
    block_reason: str | None,
) -> Event:
    """Emitted when ``frontier_dispatch_v1`` returns ``content=""`` on the
    non-exhausted branch — worst-case silent-successful-looking failure mode.

    Caller would otherwise receive ``status: completed`` with empty body.
    Handler raises ``EmptyCompletionError`` after this event, converting the
    terminal state to ``failed`` so polling callers see a structured envelope.

    Distinct from ``PipelineFrontierDispatchExhausted`` (intentional non-content
    when ``max_tool_turns`` is reached) — this fires only when the model
    genuinely returned empty content without exhausting the loop.

    Originally surfaced by Orion execution ``d65c723b`` (Cortex assertion 7903).

    Payload:
        execution_id: Pipeline execution UUID
        agent: Persona identity if set, else ``None``
        model: Raw model string as supplied by the caller
        provider: Effective provider (``anthropic``, ``openai``, ``xai``, ``google``)
        turns_used: Number of tool-call/response cycles before terminal
        tool_calls_made: Total tool invocations across all turns
        finish_reason: Provider-native finish reason, if exposed by ``NativeLoopResult``
        block_reason: Provider-native block reason, if exposed by ``NativeLoopResult``
    """
    return Event(
        signal="pipeline.frontier.dispatch.empty.completion",
        payload={
            "execution_id": execution_id,
            "agent": agent,
            "model": model,
            "provider": provider,
            "turns_used": turns_used,
            "tool_calls_made": tool_calls_made,
            "finish_reason": finish_reason,
            "block_reason": block_reason,
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
    op: str = "",
    output_contract: str = "inline",
) -> Event:
    """Emitted when a team/full dispatch returns ``output_tokens < 500``.

    Replaces the deprecated ``mcp.frontier.output.short`` signal: the same
    anomaly is now observable for every ``frontier_dispatch_v1`` caller
    (MCP sugar, pipeline callers, future HTTP surfaces) instead of only
    callers entering through ``frontier_dispatch``. Gate is ``boot_level ∈
    {team, full}`` plus the short-output threshold — enforced inside
    ``detect_output_short``. The handler passes ``boot_level="team"`` for
    persona dispatches and ``"none"`` otherwise; the detector filters the
    latter.

    Phase 3 adds an additional gate: when ``output_contract != "inline"``
    (bus-mode dispatch), the detector short-circuits before emitting so
    action-narration content is never misclassified as provider degradation.

    Payload:
        agent: Persona identity (gate: only emitted when set)
        model: Raw model string as supplied by the caller
        provider: Effective provider (``anthropic``, ``openai``, ``xai``, ``google``)
        boot_level: Internal observability vocabulary derived from agent
            presence at dispatch. NOT a caller-supplied parameter; the public
            MCP surface has no ``boot`` field.
        output_tokens: Final completion token count from the adapter usage
        tool_calls_made: Total tool invocations across all turns
        finish_reason: Provider-native finish reason, if any
        block_reason: Provider-native block reason, if any
        content_preview: First ~500 chars of content, for triage
        op: Dispatch op (``generate`` | ``to_thread`` | empty for legacy)
        output_contract: Where the work product lands (``inline`` | ``thread``)
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
            "op": op,
            "output_contract": output_contract,
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
        boot_level: Internal observability vocabulary derived from agent
            presence at dispatch. NOT a caller-supplied parameter; the public
            MCP surface has no ``boot`` field.
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
def PipelineFrontierDispatchBootMismatch(  # noqa: N802
    execution_id: str,
    agent: str,
    provider: str,
    boot_mode: str,
    reason: str,
) -> Event:
    """Emitted when the step handler rejects a (provider, boot_mode) pair that
    would cause a silent runtime tool-surface contract violation.

    Structural case: ``provider='xai'`` + ``boot_mode='team'`` (agent set,
    ``mcp_enabled=True``) — xAI multi-agent models reject client-side function
    tools; ``resolve_dispatch_tool_set`` would silently return ``tools=[]``
    while the caller expected a tool-capable dispatch.

    This is a contract-enforcement gate fired before hydration. The prompt
    layer (``build_subagent_preamble`` + ``CORTEX_TOOL_QUICKREF``) is not
    affected by this check — prompt-layer suppression is a separate follow-up.

    Precedes the terminal ``pipeline_execution_failed`` carrying
    ``code=boot_provider_mismatch``.

    Payload:
        execution_id: Pipeline execution UUID
        agent: Agent slug (e.g. ``oppie``, ``orion``)
        provider: Effective provider (``xai``, ``openai``, etc.)
        boot_mode: Internal handler-derived dispatch tier that caused the
            violation (``team`` for persona dispatches). NOT a caller-supplied
            value; derived at the handler from agent presence. The public MCP
            surface has no ``boot`` parameter — see ``team_dispatch`` /
            ``frontier_dispatch``.
        reason: Human-readable explanation including fix guidance
    """
    return Event(
        signal="pipeline.frontier.dispatch.boot.mismatch",
        payload={
            "execution_id": execution_id,
            "agent": agent,
            "provider": provider,
            "boot_mode": boot_mode,
            "reason": reason,
        },
        scope="node",
    )


@event_factory
def PipelineFrontierDispatchAgentModelMismatch(  # noqa: N802
    execution_id: str,
    agent: str,
    requested_model: str,
    valid_family: list[str],
    mismatch_kind: str,
) -> Event:
    """Emitted when the dispatch handler rejects an agent + model combination.

    Two structurally distinct failure modes share this signal, distinguished
    by ``mismatch_kind``:

    - ``"provider"``: model's provider does not match a concrete
      family/platform seat (e.g. ``grok-api-multi`` + anthropic model).
      Functional roles are model-agnostic and do not emit this mismatch.
    - ``"variant"``: provider matches but the model fails the agent's
      variant requirement (e.g. oppie + non-multi-agent xAI model).
      Suggests a stale model pin or missing beta-gate access.

    Precedes the terminal ``pipeline_execution_failed``. Distinguishes agent
    misconfiguration from MCP misconfiguration or upstream provider errors.

    Payload:
        execution_id:  Pipeline execution UUID
        agent:         Seat slug that was specified (for example ``grok-api-multi``)
        requested_model: Model string the caller supplied
        valid_family:  Allowed model identifiers for this agent
        mismatch_kind: ``"provider"`` | ``"variant"``
    """
    return Event(
        signal="pipeline.frontier.dispatch.mismatch",
        payload={
            "execution_id": execution_id,
            "agent": agent,
            "requested_model": requested_model,
            "valid_family": valid_family,
            "mismatch_kind": mismatch_kind,
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
    op: str = "",
    finish_reason: str | None = None,
    block_reason: str | None = None,
    enforcement: str = "client",
    exhaustion_summary: dict | None = None,
) -> Event:
    """Emitted when the tool loop hits ``max_tool_turns`` without terminal content.

    Signals a misbehaving dispatch: the model kept requesting tools past the
    configured budget. Content returned to the caller may be empty or the
    last assistant message that still included tool_calls.

    Two enforcement paths fire this signal:
    - ``enforcement="client"`` (default): the in-process ``run_native_tool_loop``
      cut the loop at ``max_turns``. ``result.exhausted`` is True.
    - ``enforcement="provider"``: a provider-managed loop (remote-MCP) returned
      ``content=""`` with ``finish_reason in {"tool_calls", "length"}``. The
      pipeline never saw an explicit exhausted flag, but the response shape is
      indistinguishable from a ceiling hit.

    Payload:
        op: Dispatch op (``generate`` | ``to_thread`` | empty for legacy)
        finish_reason: Provider-native finish reason on the final response.
        block_reason: Provider-native block reason, if any.
        enforcement: ``client`` | ``provider`` — which side stopped the loop.
    """
    return Event(
        signal="pipeline.frontier.dispatch.exhausted",
        payload={
            "agent": agent,
            "execution_id": execution_id,
            "turns_used": turns_used,
            "tool_calls_made": tool_calls_made,
            "provider": provider,
            "op": op,
            "finish_reason": finish_reason,
            "block_reason": block_reason,
            "enforcement": enforcement,
            "exhaustion_summary": exhaustion_summary,
        },
        scope="node",
    )


@event_factory
def PipelineFrontierDispatchToolSuppressed(  # noqa: N802
    execution_id: str,
    agent: str | None,
    model: str,
    provider: str,
    reason: str,
) -> Event:
    """Emitted when agent-tier demotion forces the tool surface to empty.

    Primary trigger: ``capability_tier == "inline-only"`` on the dispatched
    role's Cortex entity (``role:{slug}.attributes.capability_tier``).
    This gate is orthogonal to the provider-derived xAI multi-agent suppression,
    which coerces ``tools=[]`` silently without emitting this event.

    Reinstatement is a single Cortex entity-attribute update; no code change.
    Callers see normal success; telemetry visible to operators via observability
    or recent-events queries.

    NOTE: The xAI multi-agent branch in ``resolve_dispatch_tool_set`` does NOT
    emit this event. If xAI suppression should also be observable, add a
    ``publish(PipelineFrontierDispatchToolSuppressed(...))`` call to that branch
    with ``reason="provider_xai_multi_agent"``.
    """
    return Event(
        signal="pipeline.frontier.dispatch.tool.suppressed",
        payload={
            "execution_id": execution_id,
            "agent": agent,
            "model": model,
            "provider": provider,
            "reason": reason,
        },
        scope="node",
    )


@event_factory
def PipelineFrontierDispatchToolListSupplied(  # noqa: N802
    execution_id: str,
    agent: str | None,
    model: str,
    provider: str,
    tool_count: int,
    tool_names: list[str],
) -> Event:
    """Emitted when a caller passes an explicit ``pipeline_options.tools`` list.

    Soft invariant violation per Kaywan 2026-05-01 (Cortex assertion 7974):
    *"tools are not a concern to any agent or human — all tools are available
    by default."* The dispatch infrastructure exposes the full MCP catalog
    when ``mcp=True``; explicit ``tools`` lists pin a narrower surface than
    the system would otherwise provide and bypass the universal-catalog
    contract.

    The list is honored (no rejection — soft, not hard, invariant) so legacy
    callers continue to function while the pattern is surfaced for retirement.
    Track via ``observability(operation='recent-events',
    params={'signal': 'pipeline.frontier.dispatch.tools.supplied'})``
    to identify call sites still relying on the explicit-tools escape hatch.

    Signal segment-count: capped at 5 by ``EVENT_SIGNAL_PATTERN`` in
    ``libs/universal_event_bus/events/validation.py`` — hence ``tools``
    (plural) collapses what would otherwise be ``tool.list.supplied`` (6).
    """
    return Event(
        signal="pipeline.frontier.dispatch.tools.supplied",
        payload={
            "execution_id": execution_id,
            "agent": agent,
            "model": model,
            "provider": provider,
            "tool_count": tool_count,
            "tool_names": tool_names,
        },
        scope="node",
    )
