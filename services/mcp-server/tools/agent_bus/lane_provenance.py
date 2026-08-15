"""Advisory provenance observations for agent-bus lane birth."""

from __future__ import annotations

from mcp_events import record


def observe_unparented_birth(
    *,
    new_slug: str | None,
    parent_thread: str | None,
    lane_role: str | None,
    request_id: str | None,
) -> None:
    """Record a new lane that has no complete parent association."""
    if not new_slug or (parent_thread and lane_role):
        return
    record(
        "mcp.agentbus.request.lane_unparented",
        new_slug=new_slug,
        parent_thread=parent_thread or "",
        lane_role=lane_role or "",
        request_id=request_id or "",
    )
