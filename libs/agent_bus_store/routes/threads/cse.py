"""CSE association routes — host-side writes so MCP does not open messages.db."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from pydantic import BaseModel

from ...db.cse_associations import associate_cse, get_current_cse
from ...db.threads import normalize_thread_id
from . import router


class CseAssociateBody(BaseModel):
    """Append-only CSE bind. Registration without a Cowork URL is a no-op."""

    cse_chat_url: str | None = None
    cse_registration_id: str | None = None
    bound_by: str | None = None
    evidence: str | None = None


@router.post("/threads/{thread_id}/cse-associate")
async def cse_associate_route(thread_id: str, body: CseAssociateBody) -> dict[str, Any]:
    """Append one CSE association on the host store.

    Not an MCP verb. The container relays here; it must not open messages.db.
    """
    thread_id = normalize_thread_id(thread_id)
    try:
        result = associate_cse(
            thread_id=thread_id,
            cse_chat_url=body.cse_chat_url,
            cse_registration_id=body.cse_registration_id,
            bound_by=body.bound_by,
            evidence=body.evidence,
        )
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found",
        ) from None
    if result is None:
        return {"thread_id": thread_id, "state": "unchanged"}
    return result


@router.get("/threads/{thread_id}/cse-current")
async def cse_current_route(thread_id: str) -> dict[str, Any]:
    """Derived current CSE bind. Host store only — MCP relays, never opens the db."""
    thread_id = normalize_thread_id(thread_id)
    try:
        return get_current_cse(thread_id=thread_id)
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found",
        ) from None
