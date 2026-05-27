"""Frontier-dispatch tool-loop event factories.

Covers per-tool-call lifecycle plus suppression + supplied-list. Node-scoped
signals (``scope="node"``) covering the per-tool-call lifecycle inside
``run_native_tool_loop`` and the soft-invariant signals for tool-surface
suppression and explicit-tools-list usage.

The ``tool.requested`` / ``tool.called`` / ``tool.failed`` triple is correlated
by ``tool_call_id`` (Anthropic ``content_block.id``, OpenAI/xAI ``item.id``;
Google has no native id and emits ``None``).

Consumers:
- ``core/handlers/frontier_dispatch_streaming.py`` — Requested, Called, Failed
  (lazy imports)
- ``core/handlers/frontier_dispatch_admission.py`` — Suppressed (admission path)
- ``core/handlers/frontier_dispatch_tools.py`` — ToolListSupplied, Suppressed
  (lazy)

Signals: ``pipeline.frontier.dispatch.{tool.requested,tool.called,tool.failed,
tool.suppressed,tools.supplied}``.

Signal segment-count: capped at 5 by ``EVENT_SIGNAL_PATTERN`` in
``libs/universal_event_bus/events/validation.py`` — hence ``tools`` (plural)
collapses what would otherwise be ``tool.list.supplied`` (6 segments).
"""

from __future__ import annotations

from universal_event_bus import Event, event_factory


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
