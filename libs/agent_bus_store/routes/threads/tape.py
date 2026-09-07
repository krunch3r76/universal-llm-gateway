"""Tape render route for continuity lanes."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Query, status
from openapi_mcp.binding import x_mcp

from ...checkpoint_auto_stamp_wiring import load_thread_tags
from ...tape_render import render_tape
from ...thread_classification import classify_thread
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
    tags = load_thread_tags(thread_id)
    if classify_thread(tags)["spine"] != "root":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": f"thread {thread_id!r} is not a root continuity lane",
                "reason": "not_root",
                "code": "tape.not_root",
            },
        )
    try:
        return render_tape(thread_id=thread_id, budget_bytes=budget_bytes)
    except HTTPException:
        raise
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"reason": str(exc), "code": "tape.not_found"},
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"reason": str(exc), "code": "tape.render_failed"},
        ) from exc
