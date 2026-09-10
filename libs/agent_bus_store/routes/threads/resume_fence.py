"""Resume fence routes — bundle pour and hook deny journal."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from openapi_mcp.binding import x_mcp
from pydantic import BaseModel, Field

from ...checkpoint_auto_stamp_wiring import load_thread_tags
from ...db import get_thread, normalize_thread_id
from ...resume_fence import assemble_resume_fence
from ...resume_fence_store import (
    fold_fence,
    journal_denied,
    maybe_expire_idle_fence,
    release_fence,
)
from ...thread_classification import classify_thread
from . import router


class ResumeFenceCreate(BaseModel):
    """Body for POST /threads/{thread_id}/resume-fence."""

    transcript_id: str | None = None
    source: str = "mcp"
    pool: str | None = None


class FenceDeniedCreate(BaseModel):
    """Hook-reported deny payload."""

    surface: str
    tool: str = ""
    op: str = ""
    target: str = ""
    reason: str = ""
    fail_closed: bool = Field(default=True)


@router.post(
    "/threads/{thread_id}/resume-fence",
    openapi_extra=x_mcp("resume_fence", tool="continuity"),
)
async def create_resume_fence(
    thread_id: str,
    body: ResumeFenceCreate,
) -> dict[str, Any]:
    """Arm+pour a resume bundle for a continuity root thread."""
    thread_id = normalize_thread_id(thread_id)
    row = get_thread(thread_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found",
        )
    tags = load_thread_tags(thread_id)
    if classify_thread(tags)["spine"] != "root":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "resume_fence.not_root",
                "reason": "resume_fence.not_root",
                "thread": thread_id,
            },
        )
    bundle = assemble_resume_fence(
        thread_id,
        transcript_id=body.transcript_id,
        source=body.source,
        pool=body.pool,
    )
    if bundle.get("error"):
        reason = bundle.get("reason", bundle["error"])
        if reason == "resume_fence.no_tip_checkpoint":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": reason,
                    "reason": reason,
                    "thread": thread_id,
                },
            )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=bundle,
        )
    return bundle


@router.get("/resume-fences/{fence_id}")
async def get_resume_fence(fence_id: str) -> dict[str, Any]:
    """Return folded fence state for hook lookup."""
    folded = maybe_expire_idle_fence(fence_id)
    if folded is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "resume_fence.not_found", "fence_id": fence_id},
        )
    return {
        "fence_id": folded.fence_id,
        "root_thread": folded.root_thread,
        "transcript_id": folded.transcript_id,
        "state": folded.state,
        "last_event_at": folded.last_event_at,
    }


@router.post("/resume-fences/{fence_id}/denied")
async def post_resume_fence_denied(
    fence_id: str,
    body: FenceDeniedCreate,
) -> dict[str, str]:
    """Journal a hook-side deny (MCP/shell/file)."""
    folded = fold_fence(fence_id)
    if folded is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "resume_fence.not_found", "fence_id": fence_id},
        )
    journal_denied(
        fence_id=fence_id,
        surface=body.surface,
        tool=body.tool,
        op=body.op,
        target=body.target,
        reason=body.reason,
    )
    return {"status": "journaled", "fence_id": fence_id}


@router.post("/resume-fences/{fence_id}/expire")
async def post_resume_fence_expire(
    fence_id: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Journal idle expiry when the hook observes an open fence past the window."""
    folded = maybe_expire_idle_fence(fence_id)
    if folded is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "resume_fence.not_found", "fence_id": fence_id},
        )
    return {
        "fence_id": folded.fence_id,
        "state": folded.state,
        "last_event_at": folded.last_event_at,
    }


@router.post("/resume-fences/{fence_id}/release")
async def post_resume_fence_release(fence_id: str) -> dict[str, Any]:
    """Explicit release via continuity(op=resume_release)."""
    folded = release_fence(fence_id=fence_id, release_turn=0)
    if folded is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "resume_fence.not_found", "fence_id": fence_id},
        )
    if folded.state not in {"released", "expired"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "resume_fence.not_open",
                "reason": "resume_fence.not_open",
                "fence_id": fence_id,
                "state": folded.state,
            },
        )
    return {
        "fence_id": folded.fence_id,
        "state": folded.state,
    }
