"""Agent-bus result delivery for async-dispatched pipeline executions.

When a pipeline execution admitted via ``POST /api/v1/pipelines/dispatch``
carries a ``result_delivery`` config, the tracker calls
``deliver_result`` once — at terminal transition — to post a turn to the
configured agent-bus thread. Failures are observable (``pipeline.dispatch
.delivery.failed``) but do not mutate tracker state: the poll endpoint
still returns the result.

Invariants:
- ∀ record with ``result_delivery`` ∧ terminal transition: emit exactly
  one of ``pipeline.dispatch.delivery.sent`` or ``.failed``.
- ∀ delivery failure: tracker record is unchanged (no status flip, no
  error field mutation).
- ∀ ephemeral delivery success: attempt thread close; close failure emits
  ``pipeline.dispatch.delivery.close.failed`` but does NOT affect delivery
  status or tracker record.
- ¬retry: one-shot. Delivery is best-effort for "fire and forget"
  dispatchers; retry belongs on the dispatcher side.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, Protocol

import httpx
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client
from universal_logging import get_logger

if TYPE_CHECKING:
    from universal_event_bus import Event

    from .async_tracker import PipelineExecutionRecord

logger = get_logger(__name__)


_HTTP_TIMEOUT_S = 15.0


class _EventBusProtocol(Protocol):
    """Minimal event-bus surface used by the delivery module."""

    async def publish_nowait(self, event: Event) -> Any: ...


def _build_envelope(
    record: PipelineExecutionRecord,
    brief_summary: str | None = None,
) -> str:
    """Render the delivery body as a compact JSON pointer envelope.

    Full model output is never inlined — callers poll via the execution
    endpoint if they need the complete result.  Stays well under the
    agent-bus 8 000-char body limit for any realistic payload.
    """
    result = record.result
    error = record.error
    envelope: dict[str, Any] = {
        "execution_id": record.execution_id,
        "pipeline": record.pipeline,
        "status": record.status,
        "completed_at": record.completed_at,
        "poll": f"GET /api/v1/pipelines/executions/{record.execution_id}",
    }
    if result is not None:
        envelope["usage"] = result.usage
        envelope["duration_s"] = result.duration_s
        if result.hints:
            envelope["hints"] = result.hints
    if error is not None:
        envelope["error"] = {
            "code": error.code,
            "message": error.message,
        }
    if brief_summary is not None:
        envelope["summary"] = brief_summary
    return json.dumps(envelope, indent=2, default=str)


def _build_subject(record: PipelineExecutionRecord, override: str | None) -> str:
    """Prefer caller-supplied subject; fall back to pipeline + status."""
    if override:
        return override
    return f"async-dispatch {record.pipeline} {record.status}"


def _build_close_summary(record: PipelineExecutionRecord) -> str:
    """Auto-generate a close summary from terminal record state.

    Branches on status so cancelled/failed threads are not misread as
    clean completions in the thread audit trail.
    """
    result = record.result
    error = record.error
    duration = result.duration_s if result is not None else None
    if error is not None:
        duration_str = f" after {duration:.1f}s" if duration is not None else ""
        return f"{record.status} ({error.code}){duration_str}"
    duration_str = f" in {duration:.1f}s" if duration is not None else ""
    return f"{record.status}{duration_str}"


async def _close_thread(
    *,
    url: str,
    auth_token: str,
    thread: str,
    summary: str,
) -> tuple[int, str]:
    """PATCH /threads/{id}/close; return ``(status_code, response_text)``.

    Never raises — wraps network errors into 599 so caller's emit stays
    straight-line.
    """
    payload: dict[str, Any] = {"summary": summary, "mark_all_read": True}
    try:
        async with make_async_client(url, timeout=_HTTP_TIMEOUT_S) as client:
            response = await client.patch(
                f"/threads/{thread}/close",
                headers={"Authorization": f"Bearer {auth_token}"},
                json=payload,
            )
            return response.status_code, response.text
    except httpx.HTTPError as exc:
        logger.error("Agent-bus ephemeral close transport error: %s", exc)
        return 599, f"transport_error: {exc}"


async def _post_turn(
    *,
    url: str,
    auth_token: str,
    thread: str,
    from_agent: str,
    to_agent: str,
    subject: str,
    body: str,
    attachments: list[dict[str, Any]] | None = None,
) -> tuple[int, str]:
    """POST /turns; return ``(status_code, response_text)``.

    Never raises — wraps network errors into a 599 synthetic status so
    the caller's emit logic stays straight-line.
    """
    payload: dict[str, Any] = {
        "thread": thread,
        "from": from_agent,
        "to": to_agent,
        "subject": subject,
        "body": body,
    }
    if attachments:
        payload["attachments"] = attachments
    try:
        async with make_async_client(url, timeout=_HTTP_TIMEOUT_S) as client:
            response = await client.post(
                "/turns",
                headers={"Authorization": f"Bearer {auth_token}"},
                json=payload,
            )
            return response.status_code, response.text
    except httpx.HTTPError as exc:
        logger.error("Agent-bus delivery transport error: %s", exc)
        return 599, f"transport_error: {exc}"


async def deliver_result(
    record: PipelineExecutionRecord,
    *,
    event_bus: _EventBusProtocol | None,
    auth_token: str,
    url: str = DEFAULT_AGENT_BUS_URL,
) -> None:
    """Post a bus turn carrying this record's terminal state."""
    cfg = record.result_delivery or {}
    thread = cfg.get("bus_thread")
    from_agent = cfg.get("bus_from_agent")
    to_agent = cfg.get("bus_to_agent")
    subject_override = cfg.get("bus_subject")

    if not (thread and from_agent and to_agent):
        _emit(
            event_bus,
            _build_skipped_event(
                pipeline_id=record.pipeline,
                execution_id=record.execution_id,
                reason="incomplete_delivery_config",
            ),
        )
        return

    body = _build_envelope(record, brief_summary=cfg.get("bus_brief_summary"))
    subject = _build_subject(record, subject_override)
    status_code, response_text = await _post_turn(
        url=url,
        auth_token=auth_token,
        thread=thread,
        from_agent=from_agent,
        to_agent=to_agent,
        subject=subject,
        body=body,
        attachments=cfg.get("bus_attachments"),
    )

    if 200 <= status_code < 300:
        _emit(
            event_bus,
            _build_sent_event(
                pipeline_id=record.pipeline,
                execution_id=record.execution_id,
                thread=thread,
                to_agent=to_agent,
                from_agent=from_agent,
            ),
        )
        if cfg.get("bus_lifecycle") == "ephemeral":
            summary = _build_close_summary(record)
            close_code, close_text = await _close_thread(
                url=url,
                auth_token=auth_token,
                thread=thread,
                summary=summary,
            )
            if 200 <= close_code < 300:
                _emit(
                    event_bus,
                    _build_thread_closed_event(thread=thread),
                )
            else:
                logger.error(
                    "Ephemeral thread close failed: execution_id=%s thread=%s "
                    "status=%d body=%s",
                    record.execution_id,
                    thread,
                    close_code,
                    close_text[:300],
                )
                _emit(
                    event_bus,
                    _build_close_failed_event(
                        pipeline_id=record.pipeline,
                        execution_id=record.execution_id,
                        thread=thread,
                        status_code=close_code,
                        error_preview=close_text[:300],
                    ),
                )
        return

    logger.error(
        "Agent-bus delivery failed: execution_id=%s status=%d body=%s",
        record.execution_id,
        status_code,
        response_text[:300],
    )
    _emit(
        event_bus,
        _build_failed_event(
            pipeline_id=record.pipeline,
            execution_id=record.execution_id,
            thread=thread,
            status_code=status_code,
            error_preview=response_text[:300],
        ),
    )


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
) -> Event:
    from ..events.delivery import PipelineDispatchDeliverySent

    return PipelineDispatchDeliverySent(
        pipeline_id=pipeline_id,
        execution_id=execution_id,
        thread=thread,
        to_agent=to_agent,
        from_agent=from_agent,
    )


def _build_failed_event(
    *,
    pipeline_id: str,
    execution_id: str,
    thread: str,
    status_code: int,
    error_preview: str,
) -> Event:
    from ..events.delivery import PipelineDispatchDeliveryFailed

    return PipelineDispatchDeliveryFailed(
        pipeline_id=pipeline_id,
        execution_id=execution_id,
        thread=thread,
        status_code=status_code,
        error_preview=error_preview,
    )


def _build_skipped_event(*, pipeline_id: str, execution_id: str, reason: str) -> Event:
    from ..events.delivery import PipelineDispatchDeliverySkipped

    return PipelineDispatchDeliverySkipped(
        pipeline_id=pipeline_id,
        execution_id=execution_id,
        reason=reason,
    )


def _build_thread_closed_event(*, thread: str) -> Event:
    from ..events.delivery import AgentBusThreadClosedEphemeral

    return AgentBusThreadClosedEphemeral(thread=thread)


def _build_close_failed_event(
    *,
    pipeline_id: str,
    execution_id: str,
    thread: str,
    status_code: int,
    error_preview: str,
) -> Event:
    from ..events.delivery import PipelineDispatchDeliveryCloseFailed

    return PipelineDispatchDeliveryCloseFailed(
        pipeline_id=pipeline_id,
        execution_id=execution_id,
        thread=thread,
        status_code=status_code,
        error_preview=error_preview,
    )
