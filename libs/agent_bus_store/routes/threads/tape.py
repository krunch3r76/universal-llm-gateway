"""Tape render route for continuity lanes."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from continuity_tape.messages import Tools
from fastapi import HTTPException, Query, status
from openapi_mcp.binding import x_mcp

from ...checkpoint_auto_stamp_wiring import load_thread_tags
from ...tape_harvest import render_tape_with_harvest
from ...thread_classification import classify_thread
from . import router


@router.get(
    "/threads/{thread_id}/tape",
    openapi_extra=x_mcp("tape", tool="agent_bus_read"),
)
async def tape_route(
    thread_id: str,
    budget_bytes: int = Query(512_000, ge=1024, le=8_000_000),
    harvest: bool = Query(False),
    max_seals: int = Query(8, ge=0, le=64),
    scope: Literal["last_session", "full"] = Query(
        "last_session",
        description=(
            "last_session (default): pour the posting interval between the prior "
            "CHECKPOINT and the tip CP window. full: entire lane tape."
        ),
    ),
    include_extras: bool = Query(
        False,
        description="When true, mechanical extras remain on messages[].",
    ),
    tools: Tools = Query(
        "none",
        description="Tool surface policy: none | marker | openai.",
    ),
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
        return await asyncio.to_thread(
            render_tape_with_harvest,
            thread_id=thread_id,
            budget_bytes=budget_bytes,
            harvest=harvest,
            max_seals=max_seals,
            include_extras=include_extras,
            tools=tools,
            scope=scope,
        )
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
