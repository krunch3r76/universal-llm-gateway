"""Fire-and-forget event emit + lazy event factories for delivery signals.

``_emit`` is the single fan-out helper — fires ``bus.publish_nowait(event)``
inside a background task and never raises. ``bus`` may be ``None`` (silent
no-op) so the delivery package can run in test environments without a wired
event bus.

The ``_build_*_event`` factories lazy-import their concrete event classes
from ``...events.delivery`` inside the function body. This preserves the
established lazy-import pattern that defers ``core.events.delivery``
resolution until first publish — avoids import-cycle risk through the
events module on cold start.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from universal_logging import get_logger

from .protocol import _EventBusProtocol

if TYPE_CHECKING:
    from universal_event_bus import Event

logger = get_logger(__name__)


def _emit(bus: _EventBusProtocol | None, event: Event) -> None:
    """Fire-and-forget publish to the event bus; silent no-op if missing."""
    if bus is None:
        return
    try:
        asyncio.create_task(bus.publish_nowait(event))
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("Failed to publish delivery event: %s", exc)


def _build_sent_event(
    *,
    pipeline_id: str,
    execution_id: str,
    thread: str,
    to_agent: str,
    from_agent: str,
    op: str = "",
    output_contract: str = "inline",
    delivery_mode: str = "inline",
    sidecar_uri: str | None = None,
    content_sha256: str | None = None,
    sidecar_status: str = "ok",
) -> Event:
    from ...events.delivery import PipelineDispatchDeliverySent

    return PipelineDispatchDeliverySent(
        pipeline_id=pipeline_id,
        execution_id=execution_id,
        thread=thread,
        to_agent=to_agent,
        from_agent=from_agent,
        op=op,
        output_contract=output_contract,
        delivery_mode=delivery_mode,
        sidecar_uri=sidecar_uri,
        content_sha256=content_sha256,
        sidecar_status=sidecar_status,
    )


def _build_failed_event(
    *,
    pipeline_id: str,
    execution_id: str,
    thread: str,
    status_code: int,
    error_preview: str,
    op: str = "",
    output_contract: str = "inline",
) -> Event:
    from ...events.delivery import PipelineDispatchDeliveryFailed

    return PipelineDispatchDeliveryFailed(
        pipeline_id=pipeline_id,
        execution_id=execution_id,
        thread=thread,
        status_code=status_code,
        error_preview=error_preview,
        op=op,
        output_contract=output_contract,
    )


def _build_skipped_event(
    *,
    pipeline_id: str,
    execution_id: str,
    reason: str,
    op: str = "",
    output_contract: str = "inline",
) -> Event:
    from ...events.delivery import PipelineDispatchDeliverySkipped

    return PipelineDispatchDeliverySkipped(
        pipeline_id=pipeline_id,
        execution_id=execution_id,
        reason=reason,
        op=op,
        output_contract=output_contract,
    )


def _build_thread_closed_event(*, thread: str) -> Event:
    from ...events.delivery import AgentBusThreadClosedEphemeral

    return AgentBusThreadClosedEphemeral(thread=thread)


def _build_close_failed_event(
    *,
    pipeline_id: str,
    execution_id: str,
    thread: str,
    status_code: int,
    error_preview: str,
) -> Event:
    from ...events.delivery import PipelineDispatchDeliveryCloseFailed

    return PipelineDispatchDeliveryCloseFailed(
        pipeline_id=pipeline_id,
        execution_id=execution_id,
        thread=thread,
        status_code=status_code,
        error_preview=error_preview,
    )
