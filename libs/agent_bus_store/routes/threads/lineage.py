"""Live thread lineage route: substantiated lane children + dispatch links.

New read primitive (Stage 2 of the agent-bus provenance read-surface overhaul).
Combines what were two independent call sites — dispatch-link loading and
lane-child enumeration — behind one thread-id-keyed, side-effect-free GET.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException, status
from openapi_mcp.binding import x_mcp
from pydantic import BaseModel

from ...db import get_thread_lineage, normalize_thread_id
from . import router


class ThreadLineageDispatchLink(BaseModel):
    """One dispatch-execution link row, as exposed on the lineage response."""

    execution_id: str
    pipeline_id: str
    linked_at: datetime
    terminal_status: str | None = None
    delivery_at: datetime | None = None


class ThreadLineageChild(BaseModel):
    """One lane-substantiated child thread, as exposed on the lineage response."""

    thread_id: str
    status: str
    turn_count: int
    lane_role: str | None = None
    parent_thread_id: str | None = None


class ThreadLineageResponse(BaseModel):
    """Combined live lineage view: a thread's children plus its dispatch links."""

    thread_id: str
    children: list[ThreadLineageChild]
    dispatch_links: list[ThreadLineageDispatchLink]


@router.get(
    "/threads/{thread_id}/lineage",
    response_model=ThreadLineageResponse,
    openapi_extra=x_mcp("lineage", tool="agent_bus"),
)
async def thread_lineage_route(thread_id: str) -> ThreadLineageResponse:
    """Live, side-effect-free view of a thread's lane children + dispatch links.

    A seat holding only this thread's id can see any `lane_bind` children AND
    any dispatch admits against it, without posting a turn. Zero-side-effect —
    safe to poll. Distinct from the point-in-time CHECKPOINT projection, which
    is a durable snapshot baked into a posted turn body.
    """
    thread_id = normalize_thread_id(thread_id)
    lineage = get_thread_lineage(thread_id)
    if lineage is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found",
        )
    return ThreadLineageResponse(
        thread_id=lineage.thread_id,
        children=[
            ThreadLineageChild(
                thread_id=child.thread_id,
                status=child.status,
                turn_count=child.turn_count,
                lane_role=child.lane_role,
                parent_thread_id=child.parent_thread_id,
            )
            for child in lineage.children
        ],
        dispatch_links=[
            ThreadLineageDispatchLink(
                execution_id=link.execution_id,
                pipeline_id=link.pipeline_id,
                linked_at=datetime.fromisoformat(link.linked_at),
                terminal_status=link.terminal_status,
                delivery_at=(
                    datetime.fromisoformat(link.delivery_at)
                    if link.delivery_at
                    else None
                ),
            )
            for link in lineage.dispatch_links
        ],
    )
