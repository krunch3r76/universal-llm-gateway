"""Event factories for cortex endpoint-op gate rejections."""

from __future__ import annotations

from universal_event_bus import Event, event_factory


@event_factory
def McpCortexOpRejected(  # noqa: N802
    surface: str,
    family: str,
    op: str,
) -> Event:
    return Event(
        signal="mcp.cortex.op.rejected",
        payload={"surface": surface, "family": family, "op": op},
        scope="global",
    )
