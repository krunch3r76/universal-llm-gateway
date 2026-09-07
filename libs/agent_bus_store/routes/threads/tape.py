"""Tape render route for continuity lanes."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Query, status
from openapi_mcp.binding import x_mcp

from ...tape_render import render_tape
from . import router


@router.get(
    "/threads/{thread_id}/tape",
    openapi_extra=x_mcp("tape", tool="agent_bus_read"),
)
async def tape_route(
    thread_id: str,
    budget_bytes: int = Query(512_000, ge=1024, le=8_000_000),
) -> dict[str, Any]:
    """Render the messages+extras continuity tape for a root lane."""
    try:
        return render_tape(thread_id=thread_id, budget_bytes=budget_bytes)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
