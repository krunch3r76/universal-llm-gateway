"""Frontier-dispatch lifecycle event factories.

Covers hydration → native-call boundary → terminal completion / empty-completion
/ exhaustion. Node-scoped signals (``scope="node"``) marking the
``frontier_dispatch_v1`` step lifecycle. The ``started`` / ``completed`` /
``exhausted`` triple gives ``_provider_health`` per-provider call and
completion rates previously derived from the deprecated
``mcp.frontier.generate.*`` signals.

Consumers:
- ``core/handlers/frontier_dispatch.py`` — Completed, EmptyCompletion,
  Exhausted, Started
- ``core/handlers/frontier_dispatch/tools.py`` — Hydrated

Signals: ``pipeline.frontier.dispatch.{hydrated,started,completed,
empty.completion,exhausted}``.
"""

from __future__ import annotations

from universal_event_bus import Event, event_factory


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
        agent: Dispatched role or seat slug (``gatherer``, ``skeptic``,
            ``claude-web``, …)
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
def PipelineFrontierDispatchStarted(  # noqa: N802
    execution_id: str,
    agent: str | None,
    model: str,
    model_entity_id: str,
    provider: str,
    boot_level: str,
    remote_mcp: bool,
    op: str = "",
    endpoint_request_id: str | None = None,
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
        model_entity_id: Canonical Cortex model entity id for the admitted model
        provider: Effective provider (``anthropic``, ``openai``, ``xai``, ``google``)
        boot_level: Internal observability vocabulary derived from agent
            presence at dispatch: ``team`` (persona dispatch) or ``none``
            (persona-free). NOT a caller-supplied parameter; the public MCP
            surface is ``team_dispatch`` only (``role=`` selects persona vs
            inline-only ``synthesizer``).
        remote_mcp: True iff adapter-level remote MCP injection is active
        op: Dispatch op (``generate`` | ``to_thread`` | empty for legacy)
        endpoint_request_id: Endpoint ``request_id`` when admitted via a
            canonical dispatch route (join key for ``dispatch.skills.*``)
    """
    payload: dict[str, object] = {
        "execution_id": execution_id,
        "agent": agent,
        "model": model,
        "model_entity_id": model_entity_id,
        "provider": provider,
        "boot_level": boot_level,
        "remote_mcp": remote_mcp,
        "op": op,
    }
    if endpoint_request_id is not None:
        payload["endpoint_request_id"] = endpoint_request_id
    return Event(
        signal="pipeline.frontier.dispatch.started",
        payload=payload,
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
    model_entity_id: str = "",
    op: str = "",
    finish_reason: str | None = None,
    block_reason: str | None = None,
    cached_tokens: int | None = None,
) -> Event:
    """Emitted when the native tool loop returns final content.

    Payload:
        agent: Persona identity if set, else ``None`` for persona-free dispatches
        turns_used: Number of model+tool rounds consumed (≥1)
        tool_calls_made: Total tool invocations across all turns
        reasoning_present: Whether the model surfaced reasoning trace text
        provider: Effective provider (``anthropic``, ``openai``, ``xai``, ``google``)
        model_entity_id: Canonical Cortex model entity id for the admitted model
        op: Dispatch op (``generate`` | ``to_thread`` | empty for legacy)
        finish_reason: Provider-native finish reason from the final response
            (``stop`` | ``tool_calls`` | ``length`` | ``end_turn`` | None).
            Surfaced so callers can distinguish a clean stop from a ceiling-hit
            (``tool_calls`` or ``length`` with empty content) without parsing
            the raw envelope.
        block_reason: Provider-native block reason, if any (Google safety stops).
        cached_tokens: Provider-reported cache hits when available; omitted when
            the provider did not report the field.
    """
    payload: dict[str, object] = {
        "agent": agent,
        "execution_id": execution_id,
        "turns_used": turns_used,
        "tool_calls_made": tool_calls_made,
        "reasoning_present": reasoning_present,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "provider": provider,
        "model_entity_id": model_entity_id,
        "op": op,
        "finish_reason": finish_reason,
        "block_reason": block_reason,
    }
    if cached_tokens is not None:
        payload["cached_tokens"] = cached_tokens
    return Event(
        signal="pipeline.frontier.dispatch.completed",
        payload=payload,
        scope="node",
    )


@event_factory
def PipelineFrontierDispatchEmptyCompletion(  # noqa: N802
    execution_id: str,
    agent: str | None,
    model: str,
    model_entity_id: str,
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
        model_entity_id: Canonical Cortex model entity id for the admitted model
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
            "model_entity_id": model_entity_id,
            "provider": provider,
            "turns_used": turns_used,
            "tool_calls_made": tool_calls_made,
            "finish_reason": finish_reason,
            "block_reason": block_reason,
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
    model_entity_id: str = "",
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
        model_entity_id: Canonical Cortex model entity id for the admitted model
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
            "model_entity_id": model_entity_id,
            "op": op,
            "finish_reason": finish_reason,
            "block_reason": block_reason,
            "enforcement": enforcement,
            "exhaustion_summary": exhaustion_summary,
        },
        scope="node",
    )
