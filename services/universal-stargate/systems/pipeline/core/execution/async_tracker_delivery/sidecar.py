"""Thin Stargate client for on-behalf cortex thread sidecar writes.

Used by ``on_behalf.py`` during ``op="to_thread"`` async dispatch delivery: before
posting a dispatch result to an agent-bus thread, Stargate persists the full content as
a durable cortex sidecar via the ``thread_sidecar_write`` tool (through ``cx_async``).
The returned ``SidecarResult`` (URI, sha256, body length) lets oversized results be
replaced by a relocation pointer. Failures are logged and surfaced as ``None``, never
raised.
"""

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
    """Persist a dispatch result as a cortex thread sidecar on behalf of the dispatched
    agent.

    Calls cortex tool ``thread_sidecar_write`` with the thread, subject, content, the
    record's ``from_agent`` (default ``"dispatch"``), ``execution_id`` and the
    ``oversized`` flag (content exceeds the bus body limit). Returns ``SidecarResult``
    on success; logs and returns ``None`` when cortex reports an error. ``on_behalf``
    treats a ``None`` result for oversized content as a terminal delivery failure.
    """
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
