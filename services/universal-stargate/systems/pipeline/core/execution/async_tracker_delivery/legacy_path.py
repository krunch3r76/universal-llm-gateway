"""Legacy delivery path — ``result_delivery`` envelope POST + ephemeral close.

Activated when ``record.op is None`` and ``record.result_delivery`` carries a
complete config (``bus_thread`` + ``bus_from_agent`` + ``bus_to_agent``). Posts
a compact metadata envelope (built by ``envelope._build_envelope``) to the
configured thread at terminal transition.

Lifecycle semantics:

- Incomplete config → ``skipped`` + ``incomplete_delivery_config`` event.
- 2xx POST → ``delivered`` + ``pipeline.dispatch.delivery.sent`` event. If
  ``bus_lifecycle == "ephemeral"``, follow with PATCH /threads/{id}/close;
  close 2xx → ``mcp.agentbus.thread.closed`` event; close non-2xx →
  ``pipeline.dispatch.delivery.close.failed`` event but delivery still
  returns ``delivered`` (the POST succeeded — the close failure is
  observable but non-fatal).
- Non-2xx POST → ``failed`` + ``http_{code}`` reason +
  ``pipeline.dispatch.delivery.failed`` event.

Extracted from the monolith's ``deliver_result`` body so ``deliver.py`` stays
a thin router.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from universal_logging import get_logger

from .agent_bus_http import _close_thread, _fetch_thread_close_context, _post_turn
from .delivery_events import (
    _build_close_failed_event,
    _build_failed_event,
    _build_sent_event,
    _build_skipped_event,
    _build_thread_closed_event,
    _emit,
)
from .envelope import _build_close_summary, _build_envelope, _build_subject
from .outcome import DeliveryOutcome
from .protocol import _EventBusProtocol

if TYPE_CHECKING:
    from ..async_tracker import PipelineExecutionRecord

logger = get_logger(__name__)


async def _deliver_legacy_envelope(
    record: PipelineExecutionRecord,
    *,
    event_bus: _EventBusProtocol | None,
    auth_token: str,
    url: str,
) -> DeliveryOutcome:
    """Post metadata envelope from ``result_delivery``; close if ephemeral.

    Returns ``DeliveryOutcome`` matching the legacy contract: ``skipped`` on
    incomplete config, ``delivered`` on POST 2xx (regardless of subsequent
    close status), ``failed`` + ``http_{code}`` on POST non-2xx.
    """
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
        lifecycle = cfg.get("bus_lifecycle") or record.bus_lifecycle or "ephemeral"
        if lifecycle == "ephemeral":
            prior_summary, thread_tags = await _fetch_thread_close_context(
                thread, url=url, auth_token=auth_token
            )
            summary = _build_close_summary(
                record,
                prior_summary=prior_summary,
                tags=thread_tags,
            )
            close_code, close_text = await _close_thread(
                url=url,
                auth_token=auth_token,
                thread=thread,
                summary=summary or "",
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
