"""Thin Stargate client for on-behalf cortex thread sidecar writes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from ..async_tracker import PipelineExecutionRecord

logger = get_logger(__name__)


@dataclass
class SidecarResult:
    uri: str
    sha256: str
    body_chars: int


async def write_on_behalf_sidecar(
    record: PipelineExecutionRecord,
    *,
    content: str,
    thread: str,
    subject: str,
    oversized: bool,
) -> SidecarResult | None:
    from ...handlers.thread_persistence import cx_async

    result = await cx_async(
        "thread_sidecar_write",
        {
            "thread": thread,
            "subject": subject,
            "content": content,
            "from_agent": record.from_agent or "dispatch",
            "execution_id": record.execution_id,
            "oversized": oversized,
        },
    )
    if "error" in result:
        logger.error(
            "On-behalf sidecar write failed: execution_id=%s thread=%s error=%s",
            record.execution_id,
            thread,
            result.get("error"),
        )
        return None
    return SidecarResult(
        uri=result["uri"],
        sha256=result["sha256"],
        body_chars=result["body_chars"],
    )
