"""Frontier-dispatch tool-loop event factories.

Covers per-tool-call lifecycle plus suppression signals. Node-scoped
signals (``scope="node"``) covering the per-tool-call lifecycle inside
``run_native_tool_loop`` and tool-surface suppression telemetry.

The ``tool.requested`` / ``tool.called`` / ``tool.failed`` triple is correlated
by ``tool_call_id`` (Anthropic ``content_block.id``, OpenAI/xAI ``item.id``;
Google has no native id and emits ``None``).

Consumers:
- ``core/handlers/frontier_dispatch/streaming.py`` — Requested, Called, Failed
  (lazy imports)
- ``core/handlers/frontier_dispatch/admission_checks.py`` — Suppressed (admission path)
- ``core/handlers/frontier_dispatch/tools.py`` — Suppressed (lazy)
- ``core/handlers/frontier_dispatch/gen_params.py`` — Suppressed (server_tools knob)

Signals: ``pipeline.frontier.dispatch.{tool.requested,tool.called,tool.failed,
tool.suppressed}``.
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
    """Emitted when the tool surface is coerced to empty or server-side built-ins
    are suppressed.

    Reason vocabulary:
    - ``capability_tier_inline_only`` — role demoted to inline-only substrate
    - ``mcp_client_tool_loop_unsupported`` — model card rejects client-side MCP
      tools (boot-compat telemetry)
    - ``caller_mcp_false`` — caller passed ``mcp=False`` on an MCP-capable model
    - ``server_tools_knob`` — caller set ``server_tools=False`` while the card
      carries server-side built-ins
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
