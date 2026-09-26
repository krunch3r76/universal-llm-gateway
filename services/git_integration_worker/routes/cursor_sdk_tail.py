"""Click-time tail of one cursor-sdk dispatch. Not a snapshot input."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from services.git_integration_worker.cursor_sdk_run_lines import conversation_tail

router = APIRouter(prefix="/api/v1/cursor", tags=["cursor-sdk"])


@router.get("/dispatch/{dispatch_id}/conversation")
async def cursor_dispatch_conversation(
    dispatch_id: str,
    after: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Lines after ``after`` for one live or just-finished dispatch.

    The view holds the cursor and polls. The body is not a fold input.
    """
    return conversation_tail(dispatch_id, after)
