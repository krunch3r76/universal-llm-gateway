"""Agent-bus result delivery for async-dispatched pipeline executions.

Two delivery paths gated on ``record.op``:

**Legacy path** (``record.op is None``):
  When a record carries a ``result_delivery`` config, post a compact
  metadata envelope turn to the configured thread at terminal transition.
  Failures are observable but do not mutate tracker state.

**Bus-mode path** (``record.op == "to_thread"``):
  Stargate posts ``record.result.content`` to ``record.target_thread`` on
  behalf of the dispatched role/model. ``from_agent`` is supplied at
  admission (role for team_dispatch, model identifier for
  frontier_dispatch); ``to_agent`` is resolved from ``record.caller_agent``
  with a thread last-turn-from fallback. Long content (> ~1.5 KB)
  passes ``allow_long_body=true`` so the agent-bus briefing-rule warning
  is suppressed. Content above the bus 8 000-char hard limit fails with
  ``content_exceeds_bus_limit`` (poll ``pipeline(op="result")`` for the
  full text; sidecar-write fallback is a v2 follow-up).

  This replaces the previous "observe the model self-posting" contract
  (architectural fix 2026-05-22 —
  ``notes/system/decisions/dispatch-to-thread-delivery-architecture-2026-05-22.md``).
  That contract failed structurally for ``mcp=False`` dispatches (no
  agent_bus tool available) and for tool-budget-exhausted dispatches
  (model ran out of turns before posting), as observed on executions
  ``9d970982`` and ``8c1df5d3``.

Invariants:
- ∀ legacy record with ``result_delivery`` ∧ terminal transition: emit
  exactly one of ``.sent`` or ``.failed``.
- ∀ bus-mode record with non-empty ``result.content``: emit ``.sent`` on
  POST 2xx, ``.failed`` on POST non-2xx or content-too-large.
- ∀ bus-mode record with empty ``result.content``: skip POST and emit
  ``.skipped`` — the record is already ``failed`` by EmptyCompletionError.
- ∀ delivery failure: tracker record mutation is the caller's
  responsibility (see ``async_tracker._run_delivery_with_outcome``).
- ¬retry: one-shot per terminal transition.
"""

from __future__ import annotations

import asyncio
import json
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
# Bus turn body hard limit. Mirrors
# ``libs/agent_bus_store/turns_models.MAX_TURN_BODY_CHARS`` — kept as a
# module-level constant rather than imported so this module stays
# self-contained for testing without the agent_bus_store dependency tree.
_BUS_MAX_BODY_CHARS = 8_000
# Threshold below which we post inline without allow_long_body=true. Above
# this, opt into long-body to suppress the briefing-rule 413 envelope; under
# the hard limit either way.
_BUS_BRIEFING_RULE_CHARS = 1_500


@dataclass
class DeliveryOutcome:
    """Return value from ``deliver_result`` — allows the tracker to act on failures.

    ``status`` is one of:
    - ``"delivered"``: success (envelope posted or on-behalf reply landed).
    - ``"failed"``: delivery attempt failed (see ``failure_reason``).
    - ``"skipped"``: no delivery config present; no attempt was made.

    The tracker's ``_run_delivery_with_outcome()`` consults this to demote
    a ``op="to_thread"`` record from ``completed`` to ``failed`` on any
    non-delivered outcome.
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
    """Render the legacy delivery body as a compact JSON pointer envelope.

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


def _build_on_behalf_subject(record: PipelineExecutionRecord) -> str:
    """Auto-derive the reply turn subject when none was caller-supplied."""
    if record.reply_subject:
        return record.reply_subject
    short_id = record.execution_id[:8]
    actor = record.from_agent or "dispatch"
    return f"{actor} reply — execution {short_id}"


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
    allow_long_body: bool = False,
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
    if allow_long_body:
        payload["allow_long_body"] = True
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


async def _fetch_thread_last_turn_from(
    thread: str, *, url: str, auth_token: str
) -> str | None:
    """Return the agent who posted the most recent turn; ``None`` on error/empty.

    Used by the on-behalf delivery path as a ``to_agent`` fallback when
    ``record.caller_agent`` is unset.
    """
    headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}
    try:
        async with make_async_client(url, timeout=5.0) as client:
            resp = await client.get(f"/threads/{thread}", headers=headers)
        if resp.status_code >= 400:
            return None
        data = resp.json()
        last_from = data.get("last_turn_from")
        return str(last_from) if last_from else None
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(
            "Thread last-turn-from fetch failed: thread=%s error=%s", thread, exc
        )
        return None


def _resolve_to_agent(
    record: PipelineExecutionRecord, *, last_turn_from: str | None
) -> str | None:
    """Pick ``to_agent`` for the on-behalf reply turn.

    Order: ``caller_agent`` (the originating dispatcher) when distinct from
    ``from_agent``; otherwise the thread's last turn author; otherwise None
    (caller must resolve — delivery fails with ``unresolved_to_agent``).
    """
    from_agent = record.from_agent
    if record.caller_agent and record.caller_agent != from_agent:
        return record.caller_agent
    if last_turn_from and last_turn_from != from_agent:
        return last_turn_from
    return None


def _utc_now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


async def _post_content_on_behalf(
    record: PipelineExecutionRecord,
    *,
    event_bus: _EventBusProtocol | None,
    auth_token: str,
    url: str,
) -> DeliveryOutcome:
    """Post ``record.result.content`` to ``record.target_thread`` as the role/model.

    Replaces the previous polling-based "observe self-post" path. The
    dispatched model produces the reply content; Stargate posts it on the
    model's behalf so delivery is deterministic regardless of the model's
    tool surface or tool-budget consumption.
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

    content = record.result.content if record.result is not None else ""
    if not content or not content.strip():
        # Empty completions are already failed by EmptyCompletionError upstream;
        # this branch handles the unexpected case where complete_execution ran
        # with empty content. Skip the post — there is nothing to deliver.
        _emit(
            event_bus,
            _build_skipped_event(
                pipeline_id=record.pipeline,
                execution_id=record.execution_id,
                reason="empty_content",
                op=record.op or "",
                output_contract=record.output_contract,
            ),
        )
        return DeliveryOutcome(status="skipped", failure_reason="empty_content")

    from_agent = record.from_agent or "dispatch"

    if len(content) > _BUS_MAX_BODY_CHARS:
        logger.warning(
            "On-behalf delivery exceeds bus body limit: execution_id=%s "
            "thread=%s body_chars=%d limit=%d",
            record.execution_id,
            thread,
            len(content),
            _BUS_MAX_BODY_CHARS,
        )
        _emit(
            event_bus,
            _build_failed_event(
                pipeline_id=record.pipeline,
                execution_id=record.execution_id,
                thread=thread,
                status_code=413,
                error_preview=(
                    f"content_exceeds_bus_limit body_chars={len(content)} "
                    f"limit={_BUS_MAX_BODY_CHARS}"
                ),
                op=record.op or "",
                output_contract=record.output_contract,
            ),
        )
        return DeliveryOutcome(
            status="failed", failure_reason="content_exceeds_bus_limit"
        )

    last_turn_from = await _fetch_thread_last_turn_from(
        thread, url=url, auth_token=auth_token
    )
    to_agent = _resolve_to_agent(record, last_turn_from=last_turn_from)
    if not to_agent:
        logger.error(
            "On-behalf delivery missing to_agent: execution_id=%s thread=%s "
            "caller_agent=%r from_agent=%r last_turn_from=%r",
            record.execution_id,
            thread,
            record.caller_agent,
            from_agent,
            last_turn_from,
        )
        _emit(
            event_bus,
            _build_failed_event(
                pipeline_id=record.pipeline,
                execution_id=record.execution_id,
                thread=thread,
                status_code=0,
                error_preview="unresolved_to_agent",
                op=record.op or "",
                output_contract=record.output_contract,
            ),
        )
        return DeliveryOutcome(status="failed", failure_reason="unresolved_to_agent")

    subject = _build_on_behalf_subject(record)
    allow_long_body = len(content) > _BUS_BRIEFING_RULE_CHARS
    status_code, response_text = await _post_turn(
        url=url,
        auth_token=auth_token,
        thread=thread,
        from_agent=from_agent,
        to_agent=to_agent,
        subject=subject,
        body=content,
        allow_long_body=allow_long_body,
    )

    if 200 <= status_code < 300:
        record.thread_reply_observed_at = _utc_now_iso()
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
        return DeliveryOutcome(status="delivered")

    logger.error(
        "On-behalf delivery POST failed: execution_id=%s thread=%s status=%d body=%s",
        record.execution_id,
        thread,
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
            op=record.op or "",
            output_contract=record.output_contract,
        ),
    )
    return DeliveryOutcome(status="failed", failure_reason=f"post_{status_code}")


async def deliver_result(
    record: PipelineExecutionRecord,
    *,
    event_bus: _EventBusProtocol | None,
    auth_token: str,
    url: str = DEFAULT_AGENT_BUS_URL,
) -> DeliveryOutcome:
    """Route delivery based on op: legacy envelope-post OR bus-mode on-behalf post.

    Returns ``DeliveryOutcome`` so the tracker can act on failures for
    ``op="to_thread"`` records (status demotion on any non-delivered outcome).
    """
    if record.op == "to_thread":
        return await _post_content_on_behalf(
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
