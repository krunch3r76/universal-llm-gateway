from __future__ import annotations

import asyncio
import random
import time
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from universal_logging import get_logger
from universal_protocol import ErrorCode, error_envelope, get_http_status, is_retryable

from src.scheduling.events import (
    RequestCompleted,
    RequestFailed,
    RequestProcessing,
)

# Import from service root to avoid "beyond top-level package" error
from src.schemas.chat_completion import ChatCompletionRequest

from ...core.streaming.wrappers import wrap_streaming_response_for_tracking

if TYPE_CHECKING:
    from ..proxy import StargateProxy

logger = get_logger(__name__)


def _is_capacity_error(exc: HTTPException) -> bool:
    """Return True iff HTTPException indicates retryable capacity/load error."""
    # 503 = resource unavailable / at capacity
    # 504 = load timeout (model may still be loading on remote)
    if exc.status_code not in (503, 504):
        return False

    detail = exc.detail
    if not isinstance(detail, dict):
        return False

    if detail.get("retryable", False):
        return True

    code = detail.get("code", "")
    return is_retryable(code)


def _get_capacity_retry_timeout_s(proxy: StargateProxy) -> float:
    """
    Return total time budget for capacity retries (seconds).

    Source of truth: stargate config `request_queue.queue_timeout`.
    """
    request_queue_config = proxy.config.get_request_queue_config()
    raw = request_queue_config.get("queue_timeout")
    if raw is None:
        logger.error(
            "request_queue.queue_timeout missing in config; using 1800s default"
        )
        return 1800.0
    try:
        timeout_s = float(raw)
    except (TypeError, ValueError):
        logger.error("Invalid request_queue.queue_timeout=%r; using 1800s default", raw)
        return 1800.0
    if timeout_s <= 0:
        logger.error(
            "Invalid request_queue.queue_timeout=%r (must be > 0); using 1800s default",
            raw,
        )
        return 1800.0
    return timeout_s


async def process_chat_completion(
    proxy: StargateProxy,
    request: Request,
    chat_request: ChatCompletionRequest,
    model_override: str | None,
    profile_override: str | None,
    disable_profile: bool,
    skip_token_counting: bool | None,
) -> Response:
    """Process a chat completion request with unified capacity retry."""
    target_model = model_override or chat_request.model
    is_pipeline = bool(proxy.pipeline_registry) and proxy.pipeline_registry.is_pipeline(
        target_model
    )

    if is_pipeline and chat_request.messages:
        last_user_msg = next(
            (msg for msg in reversed(chat_request.messages) if msg.role == "user"), None
        )
        if last_user_msg:
            logger.info(
                "🔪 Pipeline request: Truncating %d messages to last user message only",
                len(chat_request.messages),
            )
            chat_request = chat_request.model_copy(update={"messages": [last_user_msg]})

    context = await proxy.request_preparer.prepare_request(
        request,
        chat_request,
        model_override=model_override,
        profile_override=profile_override,
        disable_profile=disable_profile,
        is_pipeline=is_pipeline,
        skip_token_counting=skip_token_counting,
    )

    if proxy.monitor:
        try:
            profile_name = getattr(context, "request_profile", None)
            await proxy.monitor.log_request_info(
                original_request=context.original_request,
                request_id=context.request_id,
                selected_model=str(context.selected_model),
                profile_name=profile_name,
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.debug("Failed to send early request_info event: %s", exc)

    if is_pipeline:
        logger.info("Routing to pipeline executor: %s", context.selected_model)
        try:
            return await proxy.pipeline_executor.execute(context)
        except Exception as exc:
            from systems.pipeline.core.execution.errors import PipelineError

            if isinstance(exc, PipelineError):
                error_dict = exc.to_dict()
                logger.error(
                    "Pipeline execution failed: %s - %s",
                    error_dict.get("error_type"),
                    str(exc),
                )
                raise HTTPException(
                    status_code=500,
                    detail={
                        "error": error_dict,
                        "pipeline_id": context.selected_model,
                    },
                ) from exc
            raise

    model_id = str(context.selected_model)
    request_short_id = getattr(context, "request_id", "unknown")[:8]

    start_time = time.time()
    timeout_s = _get_capacity_retry_timeout_s(proxy)
    capacity_retry_started = time.monotonic()
    retry_count = 0

    if proxy.event_bus:
        try:
            await proxy.event_bus.publish_async_nowait(
                RequestProcessing(
                    request_id=context.request_id,
                    gateway_url=proxy.gateway_url,
                    model_id=model_id,
                )
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.debug("Failed to emit REQUEST_PROCESSING event: %s", exc)

    try:
        while True:
            try:
                response = await proxy.request_executor.execute_request(context)

                if isinstance(response, StreamingResponse):
                    response_gateway_id = context.target_gateway_id
                    if not response_gateway_id:
                        logger.error(
                            "❌ [REQ:%s] No gateway_id available for "
                            "streaming tracking (target=%s)",
                            request_short_id,
                            context.target_gateway_id,
                        )
                        response_gateway_id = "unknown-gateway"

                    return wrap_streaming_response_for_tracking(
                        response=response,
                        context=context,
                        model_id=model_id,
                        start_time=start_time,
                        event_bus=proxy.event_bus,
                        gateway_id=response_gateway_id,
                    )

                logger.info(
                    "✅ [REQ:%s] Non-streaming response completed",
                    request_short_id,
                )

                if proxy.event_bus:
                    try:
                        await proxy.event_bus.publish_async_nowait(
                            RequestCompleted(
                                request_id=context.request_id,
                                gateway_url=proxy.gateway_url,
                                model_id=model_id,
                                duration=time.time() - start_time,
                            )
                        )
                    except Exception as exc:  # pragma: no cover - defensive logging
                        logger.debug("Failed to emit REQUEST_COMPLETED event: %s", exc)

                return response

            except HTTPException as exc:
                if not _is_capacity_error(exc):
                    raise

                retry_count += 1

                elapsed = time.monotonic() - capacity_retry_started
                remaining = timeout_s - elapsed
                if remaining <= 0:
                    raise HTTPException(
                        status_code=get_http_status(ErrorCode.CAPACITY_TIMEOUT),
                        detail=error_envelope(
                            code=ErrorCode.CAPACITY_TIMEOUT,
                            message=f"Capacity timeout for model {model_id}",
                            source="master",
                            retryable=False,
                            data={
                                "model_id": model_id,
                                "timeout_seconds": timeout_s,
                                "retry_count": retry_count,
                            },
                        ),
                    ) from exc

                if await request.is_disconnected():
                    raise asyncio.CancelledError("Client disconnected")

                base_delay = min(2.0, 0.05 * (2 ** min(retry_count - 1, 6)))
                delay_s = min(base_delay * random.uniform(0.5, 1.5), remaining)

                log_level = logger.info if retry_count <= 3 else logger.debug
                log_level(
                    "🔄 [REQ:%s] Capacity retry for %s (retry #%d, sleep=%.2fs)",
                    request_short_id,
                    model_id,
                    retry_count,
                    delay_s,
                )
                await asyncio.sleep(delay_s)
                continue

    except Exception as exc:
        logger.warning(
            "⚠️ [REQ:%s] Exception in request processing: %s",
            request_short_id,
            type(exc).__name__,
        )
        if proxy.event_bus:
            try:
                await proxy.event_bus.publish_async_nowait(
                    RequestFailed(
                        request_id=context.request_id,
                        gateway_url=proxy.gateway_url,
                        model_id=model_id,
                        error=str(exc),
                    )
                )
            except Exception as emit_err:  # pragma: no cover - defensive logging
                logger.debug("Failed to emit REQUEST_FAILED event: %s", emit_err)
        raise
