"""Bus-mode (``op="to_thread"``) delivery — on-behalf content post.

Replaces the previous polling-based "observe self-post" contract
(architectural fix 2026-05-22 —
``notes/system/decisions/dispatch-to-thread-delivery-architecture-2026-05-22.md``).
That contract failed structurally for ``mcp=False`` dispatches (no
agent_bus tool available) and for tool-budget-exhausted dispatches (model
ran out of turns before posting), as observed on executions ``9d970982``
and ``8c1df5d3``.

Stargate now posts ``record.result.content`` to ``record.target_thread``
on behalf of the dispatched role/model — delivery is deterministic
regardless of the model's tool surface or tool-budget consumption.

Invariant ladder (each branch emits exactly one event and returns a
distinct ``DeliveryOutcome``):

- no ``target_thread`` → ``skipped`` + ``no_target_thread``
- empty/whitespace ``content`` → ``skipped`` + ``empty_content``
- ``content > _BUS_MAX_BODY_CHARS`` → ``failed`` + ``content_exceeds_bus_limit``
  (413 event, POST not attempted)
- unresolved ``to_agent`` (no caller_agent, no thread last_turn_from) →
  ``failed`` + ``unresolved_to_agent`` (POST not attempted)
- 2xx POST → ``delivered`` + sets ``record.thread_reply_observed_at``
- non-2xx POST → ``failed`` + ``post_{code}``
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from universal_logging import get_logger

from .agent_bus_http import _fetch_thread_last_turn_from, _post_turn
from .constants import _BUS_BRIEFING_RULE_CHARS, _BUS_MAX_BODY_CHARS
from .delivery_events import (
    _build_failed_event,
    _build_sent_event,
    _build_skipped_event,
    _emit,
)
from .envelope import _build_on_behalf_subject
from .outcome import DeliveryOutcome
from .protocol import _EventBusProtocol
from .resolution import _resolve_to_agent, _utc_now_iso

if TYPE_CHECKING:
    from ..async_tracker import PipelineExecutionRecord

logger = get_logger(__name__)


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
