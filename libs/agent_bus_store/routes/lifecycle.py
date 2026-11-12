"""Lifecycle projections on agent-bus UDS (not Stargate)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from ..db.connection import connect
from ..db.hop_precedence import get_hop_precedence

router = APIRouter()


@router.get("/lifecycle/hop-precedence")
async def hop_precedence_route(
    thread: str | None = Query(default=None),
    work_key: str | None = Query(default=None),
) -> dict[str, Any]:
    """Read-only hop-before-checkpoint precedence projection for a thread or work_key."""
    if (thread is None) == (work_key is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Exactly one of thread or work_key is required",
        )
    with connect() as conn:
        items = get_hop_precedence(conn, thread_id=thread, work_key=work_key)
    return {"items": items}
