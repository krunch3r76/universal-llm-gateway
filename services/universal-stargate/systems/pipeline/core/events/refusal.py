"""Event-bus factory flagging suspected refusals from frontier_dispatch agent runs.

``PipelineFrontierDispatchRefusalSuspected`` (signal
``pipeline.frontier.dispatch.refusal.suspected``, node scope) is published by
``handlers/frontier_dispatch/observability.py`` when a dispatch that made tool calls
ends with a short, refusal-shaped answer (below an output-token threshold and matching
refusal markers). It is an anomaly hint for observability, not an execution failure.
"""

from __future__ import annotations

from universal_event_bus import Event, event_factory


@event_factory
def PipelineFrontierDispatchRefusalSuspected(  # noqa: N802
    agent: str | None,
    execution_id: str,
    model: str,
    provider: str,
    output_tokens: int,
    tool_calls_made: int,
    content_preview: str,
    reason: str,
) -> Event:
    """Emitted when a frontier dispatch returns a short refusal after tool use."""
    return Event(
        signal="pipeline.frontier.dispatch.refusal.suspected",
        payload={
            "agent": agent,
            "execution_id": execution_id,
            "model": model,
            "provider": provider,
            "output_tokens": output_tokens,
            "tool_calls_made": tool_calls_made,
            "content_preview": content_preview,
            "reason": reason,
        },
        scope="node",
    )
