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

Every delivery writes a durable cortex sidecar first. Within the bus body
limit the turn carries full content plus a durable-copy footer; above the
limit it carries a relocation pointer (URI + sha256 + summary). The only
terminal failure without POST is oversized content when the sidecar write
also fails.

Invariant ladder (each branch emits exactly one event and returns a
distinct ``DeliveryOutcome``):

- no ``target_thread`` → ``skipped`` + ``no_target_thread``
- empty/whitespace ``content`` → ``skipped`` + ``empty_content``
- unresolved ``to_agent`` (no caller_agent, no thread last_turn_from) →
  ``failed`` + ``unresolved_to_agent`` (POST not attempted)
- oversized content ∧ sidecar write failed → ``failed`` + ``sidecar_write_failed``
  (413 event, POST not attempted)
- 2xx POST → ``delivered`` + sets ``record.thread_reply_observed_at``
- non-2xx POST → ``failed`` + ``post_{code}``
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from universal_logging import get_logger

from .agent_bus_http import _close_thread, _fetch_thread_last_turn_from, _post_turn
from .constants import _BUS_BRIEFING_RULE_CHARS, _BUS_MAX_BODY_CHARS
from .delivery_events import (
    _build_close_failed_event,
    _build_failed_event,
    _build_sent_event,
    _build_skipped_event,
    _build_thread_closed_event,
    _emit,
)
from .envelope import (
    _build_close_summary,
    _build_inline_with_reference,
    _build_on_behalf_subject,
    _build_relocation_pointer,
    _extract_pointer_summary,
)
from .outcome import DeliveryOutcome
from .protocol import _EventBusProtocol
from .resolution import _resolve_to_agent, _utc_now_iso
from .sidecar import write_on_behalf_sidecar

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
        return DeliveryOutcome(
            status="skipped", failure_reason="empty_content", thread=thread
        )

    from_agent = record.from_agent or "dispatch"

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
        return DeliveryOutcome(
            status="failed", failure_reason="unresolved_to_agent", thread=thread
        )

    subject = _build_on_behalf_subject(record)
    oversized = len(content) > _BUS_MAX_BODY_CHARS

    sidecar = await write_on_behalf_sidecar(
        record,
        content=content,
        thread=thread,
        subject=subject,
        oversized=oversized,
    )

    if oversized and sidecar is None:
        logger.error(
            "On-behalf sidecar write failed (oversized, terminal): "
            "execution_id=%s thread=%s body_chars=%d",
            record.execution_id,
            thread,
            len(content),
        )
        _emit(
            event_bus,
            _build_failed_event(
                pipeline_id=record.pipeline,
                execution_id=record.execution_id,
                thread=thread,
                status_code=413,
                error_preview=f"sidecar_write_failed body_chars={len(content)}",
                op=record.op or "",
                output_contract=record.output_contract,
            ),
        )
        return DeliveryOutcome(
            status="failed",
            failure_reason="sidecar_write_failed",
            thread=thread,
            delivery_mode="sidecar",
        )

    if oversized:
        delivery_mode = "sidecar"
        sidecar_status = "ok"
        summary = _extract_pointer_summary(content)
        body = _build_relocation_pointer(
            record,
            sidecar_uri=sidecar.uri,
            sha256=sidecar.sha256,
            body_chars=len(content),
            summary=summary,
        )
        allow_long_body = False
    else:
        delivery_mode = "inline"
        if sidecar is not None:
            sidecar_status = "ok"
            body = _build_inline_with_reference(
                content,
                sidecar_uri=sidecar.uri,
                sha256=sidecar.sha256,
            )
        else:
            sidecar_status = "failed"
            body = content
        allow_long_body = len(body) > _BUS_BRIEFING_RULE_CHARS

    status_code, response_text = await _post_turn(
        url=url,
        auth_token=auth_token,
        thread=thread,
        from_agent=from_agent,
        to_agent=to_agent,
        subject=subject,
        body=body,
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
                op=record.op or "",
                output_contract=record.output_contract,
                delivery_mode=delivery_mode,
                sidecar_uri=(sidecar.uri if sidecar is not None else None),
                content_sha256=(sidecar.sha256 if sidecar is not None else None),
                sidecar_status=sidecar_status,
            ),
        )
        if record.bus_lifecycle == "ephemeral":
            summary = _build_close_summary(record)
            close_code, close_text = await _close_thread(
                url=url,
                auth_token=auth_token,
                thread=thread,
                summary=summary,
            )
            if 200 <= close_code < 300:
                _emit(event_bus, _build_thread_closed_event(thread=thread))
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
        return DeliveryOutcome(
            status="delivered",
            delivery_mode=delivery_mode,
            thread=thread,
            sidecar_uri=(sidecar.uri if sidecar is not None else None),
            content_sha256=(sidecar.sha256 if sidecar is not None else None),
        )

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
    return DeliveryOutcome(
        status="failed",
        failure_reason=f"post_{status_code}",
        thread=thread,
        delivery_mode=delivery_mode,
        sidecar_uri=(sidecar.uri if sidecar is not None else None),
        content_sha256=(sidecar.sha256 if sidecar is not None else None),
    )
