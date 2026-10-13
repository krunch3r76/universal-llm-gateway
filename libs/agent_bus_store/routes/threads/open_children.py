"""GET /threads/{thread_id}/open-children — unharvested / active child set."""

from __future__ import annotations

import asyncio

from fastapi import HTTPException, status
from openapi_mcp.binding import x_mcp
from pydantic import BaseModel

from ...db import get_thread, normalize_thread_id
from ...open_children import compute_open_children, open_children_as_of
from . import router


class OpenChildrenResponse(BaseModel):
    """Open child thread ids for an operator lane parent."""

    thread_ids: list[str]
    as_of: str
    parent_thread_id: str


@router.get(
    "/threads/{thread_id}/open-children",
    response_model=OpenChildrenResponse,
    openapi_extra=x_mcp("open_children", tool="agent_bus"),
)
async def open_children_route(thread_id: str) -> OpenChildrenResponse:
    """Return active or unharvested depth-1 children for ``thread_id``."""
    normalized = await asyncio.to_thread(normalize_thread_id, thread_id)
    row = await asyncio.to_thread(get_thread, normalized)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {normalized} not found",
        )
    ids = await asyncio.to_thread(compute_open_children, normalized)
    return OpenChildrenResponse(
        thread_ids=list(ids),
        as_of=open_children_as_of(),
        parent_thread_id=normalized,
    )
