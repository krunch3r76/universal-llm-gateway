"""Agent-bus result delivery for async-dispatched pipeline executions.

Two delivery paths gated on ``record.op``:

**Legacy path** (``record.op is None``):
  When a record carries a ``result_delivery`` config, post a compact
  metadata envelope turn to the configured thread at terminal transition.
  Failures are observable but do not mutate tracker state.

**Bus-mode path** (``record.op == "to_thread"``, Phase 2):
  Do NOT post a metadata envelope. Instead, poll the target thread for a
  new turn — the agent's actual reply IS the durable artifact. If no turn
  is observed within ``_BUS_WRITE_TIMEOUT_S``, return
  ``DeliveryOutcome("failed", "thread_reply_not_observed")`` so the
  tracker can demote the provisional ``completed`` record to ``failed``.

Invariants:
- ∀ legacy record with ``result_delivery`` ∧ terminal transition: emit
  exactly one of ``.sent`` or ``.failed``.
- ∀ bus-mode record (``op="to_thread"``): emit ``.completed`` on
  observation, ``.failed`` on timeout; ¬envelope turn posted.
- ∀ delivery failure: tracker record mutation is the caller's
  responsibility (see ``async_tracker._run_delivery_with_outcome``).
- ¬retry: one-shot per terminal transition.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

import httpx
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client
from universal_logging import get_logger

if TYPE_CHECKING:
    from universal_event_bus import Event

    from .async_tracker import PipelineExecutionRecord

logger = get_logger(__name__)


_HTTP_TIMEOUT_S = 15.0
_BUS_WRITE_TIMEOUT_S = 30.0
_POLL_INTERVAL_S = 0.5


@dataclass
class DeliveryOutcome:
    """Return value from ``deliver_result`` — allows the tracker to act on failures.

    ``status`` is one of:
    - ``"delivered"``: success (envelope posted or reply observed).
    - ``"failed"``: delivery attempt failed (see ``failure_reason``).
    - ``"skipped"``: no delivery config present; no attempt was made.

    The tracker's ``_run_delivery_with_outcome()`` consults this when
    ``record.op == "to_thread"`` to demote a provisional ``completed``
    record to ``failed`` on ``thread_reply_not_observed`` timeouts.
    """

    status: Literal["delivered", "failed", "skipped"]
    failure_reason: str | None = None


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


async def _fetch_thread_turn_count(
    thread: str, *, url: str, auth_token: str
) -> int | None:
    """Return the thread's current turn count; ``None`` on transport/404 error."""
    headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}
    try:
        async with make_async_client(url, timeout=5.0) as client:
            resp = await client.get(f"/threads/{thread}", headers=headers)
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            logger.warning(
                "Thread turn-count fetch failed: thread=%s status=%d",
                thread,
                resp.status_code,
            )
            return None
        return int(resp.json().get("turn_count", 0))
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(
            "Thread turn-count transport error: thread=%s error=%s", thread, exc
        )
        return None


def _utc_now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


async def _observe_reply_and_record(
    record: PipelineExecutionRecord,
    *,
    event_bus: _EventBusProtocol | None,
    auth_token: str,
    url: str,
    timeout_s: float = _BUS_WRITE_TIMEOUT_S,
    poll_interval_s: float = _POLL_INTERVAL_S,
) -> DeliveryOutcome:
    """Poll target_thread for a new turn; emit observation events; return outcome.

    Phase 2 bus-mode path.  Does NOT post a metadata envelope turn — the
    agent's reply IS the durable artifact.  Polls until a new turn appears
    or ``timeout_s`` elapses.

    ``timeout_s`` uses the record's configured ``timeout_seconds`` value when
    present (callers can shorten the polling window via dispatch timeout).
    """
    thread = record.target_thread
    if not thread:
        _emit(
            event_bus,
            _build_skipped_event(
                pipeline_id=record.pipeline,
                execution_id=record.execution_id,
                reason="no_target_thread",
                op=record.op or "",
                output_contract=record.output_contract,
            ),
        )
        return DeliveryOutcome(status="skipped", failure_reason="no_target_thread")

    # Snapshot the turn count at the start of observation (after model finished).
    pre_count = await _fetch_thread_turn_count(thread, url=url, auth_token=auth_token)
    if pre_count is None:
        logger.error(
            "Bus-mode reply observation failed: thread=%s not found. execution_id=%s",
            thread,
            record.execution_id,
        )
        _emit(
            event_bus,
            _build_failed_event(
                pipeline_id=record.pipeline,
                execution_id=record.execution_id,
                thread=thread,
                status_code=404,
                error_preview="thread_not_found_during_observation",
                op=record.op or "",
                output_contract=record.output_contract,
            ),
        )
        return DeliveryOutcome(status="failed", failure_reason="thread_not_found")

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        await asyncio.sleep(poll_interval_s)
        current_count = await _fetch_thread_turn_count(
            thread, url=url, auth_token=auth_token
        )
        if current_count is not None and current_count > pre_count:
            observed_at = _utc_now_iso()
            record.thread_reply_observed_at = observed_at
            _emit(
                event_bus,
                _build_delivery_completed_event(
                    pipeline_id=record.pipeline,
                    execution_id=record.execution_id,
                    thread=thread,
                    observed_at=observed_at,
                ),
            )
            return DeliveryOutcome(status="delivered")

    logger.warning(
        "Bus-mode reply not observed: execution_id=%s thread=%s timeout=%.1fs",
        record.execution_id,
        thread,
        timeout_s,
    )
    _emit(
        event_bus,
        _build_failed_event(
            pipeline_id=record.pipeline,
            execution_id=record.execution_id,
            thread=thread,
            status_code=0,
            error_preview="thread_reply_not_observed",
            op=record.op or "",
            output_contract=record.output_contract,
        ),
    )
    return DeliveryOutcome(status="failed", failure_reason="thread_reply_not_observed")


async def deliver_result(
    record: PipelineExecutionRecord,
    *,
    event_bus: _EventBusProtocol | None,
    auth_token: str,
    url: str = DEFAULT_AGENT_BUS_URL,
) -> DeliveryOutcome:
    """Route delivery based on op: legacy envelope-post OR bus-mode reply observation.

    Returns ``DeliveryOutcome`` so the tracker can act on failures for
    ``op="to_thread"`` records (status demotion on ``thread_reply_not_observed``).
    """
    if record.op == "to_thread":
        # Phase 2 bus-mode: observe reply; ¬post metadata envelope.
        return await _observe_reply_and_record(
            record,
            event_bus=event_bus,
            auth_token=auth_token,
            url=url,
        )

    # Legacy path: result_delivery dict drives metadata envelope post.
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
        return DeliveryOutcome(
            status="skipped", failure_reason="incomplete_delivery_config"
        )

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
        return DeliveryOutcome(status="delivered")

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
    return DeliveryOutcome(status="failed", failure_reason=f"http_{status_code}")


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
    op: str = "",
    output_contract: str = "inline",
) -> Event:
    from ..events.delivery import PipelineDispatchDeliveryFailed

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
    from ..events.delivery import PipelineDispatchDeliverySkipped

    return PipelineDispatchDeliverySkipped(
        pipeline_id=pipeline_id,
        execution_id=execution_id,
        reason=reason,
        op=op,
        output_contract=output_contract,
    )


def _build_delivery_completed_event(
    *,
    pipeline_id: str,
    execution_id: str,
    thread: str,
    observed_at: str,
) -> Event:
    from ..events.delivery import PipelineDispatchDeliveryCompleted

    return PipelineDispatchDeliveryCompleted(
        pipeline_id=pipeline_id,
        execution_id=execution_id,
        thread=thread,
        observed_at=observed_at,
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
