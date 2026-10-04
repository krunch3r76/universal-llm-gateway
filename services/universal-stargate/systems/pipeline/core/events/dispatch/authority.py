"""Monitor authority read degradation signals."""

from __future__ import annotations

from universal_event_bus import Event, event_factory


@event_factory
def PipelineExecutionAuthorityUnreachable(  # noqa: N802
    execution_id: str,
    source_attempted: str,
    reason: str,
    fell_back_to: str,
) -> Event:
    """Emitted when the monitor could not consult registry execution_state."""
    return Event(
        signal="pipeline.execution.authority_unreachable",
        payload={
            "execution_id": execution_id,
            "source_attempted": source_attempted,
            "reason": reason,
            "fell_back_to": fell_back_to,
        },
        scope="node",
    )
