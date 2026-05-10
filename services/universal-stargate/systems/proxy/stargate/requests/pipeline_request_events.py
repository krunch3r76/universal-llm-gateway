"""Request lifecycle event emitters for virtual pipeline chat-completions."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

from fastapi.responses import Response
from universal_logging import get_logger

from src.scheduling.events import (
    RequestCompleted,
    RequestFailed,
    RequestProcessing,
    RequestProfileResolved,
    RequestSnapshotCompleted,
    RequestSnapshotFailed,
    RequestSnapshotRouted,
)

if TYPE_CHECKING:
    from universal_event_bus import EventBus

    from systems.proxy.core.nonstreaming.preparer import RequestContext

logger = get_logger(__name__)

_PIPELINE_GATEWAY_ID = "stargate-pipeline"


def _caller_hint(context: RequestContext) -> dict[str, Any] | None:
    hint: dict[str, Any] = {}
    http_request = context.http_request
    if http_request is not None:
        user_agent = http_request.headers.get("user-agent")
        if user_agent:
            hint["user_agent"] = user_agent[:200]
        x_caller = http_request.headers.get(
            "x-stargate-caller"
        ) or http_request.headers.get("x-caller")
        if x_caller:
            hint["x_caller"] = x_caller[:200]
    if context.pipeline_execution_id:
        hint["pipeline_execution_id"] = context.pipeline_execution_id
    if context.pipeline_step_id:
        hint["pipeline_step_id"] = context.pipeline_step_id
    return hint or None


def _response_snapshot(response: Response) -> tuple[str, dict[str, Any] | None]:
    body = getattr(response, "body", b"")
    if not isinstance(body, bytes):
        return "", None
    try:
        data = json.loads(body)
    except Exception:
        logger.warning("Pipeline response body was not JSON; snapshot omitted")
        return "", None
    choices = data.get("choices", [])
    content = ""
    if choices:
        content = str(choices[0].get("message", {}).get("content", "") or "")
    usage = data.get("usage")
    return content, usage if isinstance(usage, dict) else None


async def emit_pipeline_routed_events(
    event_bus: EventBus | None,
    context: RequestContext,
    *,
    model_id: str,
    gateway_url: str,
) -> None:
    if event_bus is None:
        return
    try:
        profile_name = getattr(context, "request_profile", None)
        if profile_name:
            await event_bus.publish_nowait(
                RequestProfileResolved(
                    request_id=context.request_id,
                    model_id=model_id,
                    profile_name=profile_name,
                )
            )
        await event_bus.publish_nowait(
            RequestProcessing(
                request_id=context.request_id,
                gateway_url=gateway_url,
                model_id=model_id,
            )
        )
        await event_bus.publish_nowait(
            RequestSnapshotRouted(
                request_id=context.request_id,
                model_id=model_id,
                gateway_id=_PIPELINE_GATEWAY_ID,
                profile_name=profile_name,
            )
        )
    except Exception as exc:
        logger.warning(
            "Failed to emit pipeline routed lifecycle events for %s (%s): %s",
            context.request_id,
            type(exc).__name__,
            exc,
            exc_info=True,
        )


async def emit_pipeline_completed_events(
    event_bus: EventBus | None,
    context: RequestContext,
    *,
    model_id: str,
    gateway_url: str,
    response: Response,
    start_time: float,
) -> None:
    if event_bus is None:
        return
    duration = time.time() - start_time
    try:
        await event_bus.publish_nowait(
            RequestCompleted(
                request_id=context.request_id,
                gateway_url=gateway_url,
                model_id=model_id,
                duration=duration,
            )
        )
        content, usage = _response_snapshot(response)
        await event_bus.publish_nowait(
            RequestSnapshotCompleted(
                request_id=context.request_id,
                model_id=model_id,
                gateway_id=_PIPELINE_GATEWAY_ID,
                content=content,
                usage=usage,
                duration_s=duration,
            )
        )
    except Exception as exc:
        logger.warning(
            "Failed to emit pipeline completed lifecycle events for %s (%s): %s",
            context.request_id,
            type(exc).__name__,
            exc,
            exc_info=True,
        )


async def emit_pipeline_failed_events(
    event_bus: EventBus | None,
    context: RequestContext,
    *,
    model_id: str,
    gateway_url: str,
    error: BaseException,
    error_detail: dict[str, Any] | None,
) -> None:
    if event_bus is None:
        return
    data = dict(error_detail) if error_detail else None
    code = data.get("code") if data else None
    try:
        caller_hint = _caller_hint(context)
        await event_bus.publish_nowait(
            RequestFailed(
                request_id=context.request_id,
                gateway_url=gateway_url,
                model_id=model_id,
                error=str(error),
                error_code=str(code) if code is not None else None,
                error_source="pipeline",
                error_data=data,
                caller_hint=caller_hint,
            )
        )
        await event_bus.publish_nowait(
            RequestSnapshotFailed(
                request_id=context.request_id,
                model_id=model_id,
                error=str(error),
                error_code=str(code) if code is not None else None,
                error_source="pipeline",
                error_data=data,
                caller_hint=caller_hint,
            )
        )
    except Exception as exc:
        logger.warning(
            "Failed to emit pipeline failed lifecycle events for %s (%s): %s",
            context.request_id,
            type(exc).__name__,
            exc,
            exc_info=True,
        )
