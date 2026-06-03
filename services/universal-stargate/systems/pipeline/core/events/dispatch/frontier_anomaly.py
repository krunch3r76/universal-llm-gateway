"""Frontier-dispatch anomaly + admission-mismatch event factories.

Node-scoped signals (``scope="node"``) for three structurally distinct
anomaly classes:

- ``output.short``: completion came back below the 500-token threshold under
  team/full boot levels (provider-degradation signal). Replaces the
  deprecated ``mcp.frontier.output.short`` signal.
- ``termination.shadow``: Gemini thought trace looks like a silent termination
  (refusal/incapacity/policy/scope/exhaustion/loop). Replaces the deprecated
  ``mcp.frontier.thought.termination.shadow`` signal.
- ``mismatch``: agent + model admission rejection (provider mismatch or
  variant mismatch). Precedes a terminal ``pipeline_execution_failed``.

Consumers:
- ``core/handlers/frontier_dispatch_observability.py`` — OutputShort, TerminationShadow
- ``core/handlers/frontier_dispatch_admission.py`` — AgentModelMismatch

Signals: ``pipeline.frontier.dispatch.{output.short,termination.shadow,mismatch}``.
"""

from __future__ import annotations

from universal_event_bus import Event, event_factory


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
def PipelineFrontierDispatchAgentModelMismatch(  # noqa: N802
    execution_id: str,
    agent: str,
    requested_model: str,
    model_entity_id: str,
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
      variant requirement (e.g. skeptic + non-multi-agent xAI model).
      Suggests a stale model pin or missing beta-gate access.

    Precedes the terminal ``pipeline_execution_failed``. Distinguishes agent
    misconfiguration from MCP misconfiguration or upstream provider errors.

    ``model_entity_id`` is included so post-hoc correlators can recover
    the canonical Cortex ``model:<slug>`` directly from this event when
    ``.started`` is absent — the mismatch branch fires during admission
    when the handler rejects an incompatible agent + model combination,
    leaving ``execution_id`` without an outcome event to join against.
    Mirrors the recovery shape used on
    ``pipeline.frontier.dispatch.remotemcp.misconfigured``.

    Payload:
        execution_id:    Pipeline execution UUID
        agent:           Seat slug that was specified (for example ``grok-api-multi``)
        requested_model: Model string the caller supplied
        model_entity_id: Canonical Cortex model entity id for the requested model
        valid_family:    Allowed model identifiers for this agent
        mismatch_kind:   ``"provider"`` | ``"variant"``
    """
    return Event(
        signal="pipeline.frontier.dispatch.mismatch",
        payload={
            "execution_id": execution_id,
            "agent": agent,
            "requested_model": requested_model,
            "model_entity_id": model_entity_id,
            "valid_family": valid_family,
            "mismatch_kind": mismatch_kind,
        },
        scope="node",
    )
