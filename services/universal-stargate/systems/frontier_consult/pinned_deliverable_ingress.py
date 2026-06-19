"""Stargate ingress for cursor-sdk pinned cortex deliverable writes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from universal_logging import get_logger

logger = get_logger(__name__)


class PinnedDeliverableBody(BaseModel):
    model_config = {"extra": "forbid"}

    rel_path: str = Field(min_length=1)
    content: str
    write_if_absent: bool = False
    dispatch_id: str | None = None
    thread_id: str | None = None


async def write_pinned_deliverable_via_cortex(
    body: PinnedDeliverableBody,
) -> dict[str, Any]:
    from systems.pipeline.core.handlers.thread_persistence.events import cx_async

    result = await cx_async(
        "pinned_deliverable_write",
        {
            "rel_path": body.rel_path,
            "content": body.content,
            "write_if_absent": body.write_if_absent,
            "dispatch_id": body.dispatch_id,
            "thread_id": body.thread_id,
        },
    )
    if "error" in result:
        logger.warning(
            "pinned deliverable write failed: rel=%s dispatch=%s err=%s",
            body.rel_path,
            body.dispatch_id,
            result.get("error"),
        )
    return result
