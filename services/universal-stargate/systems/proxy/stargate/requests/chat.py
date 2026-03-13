from __future__ import annotations

import asyncio
import random
import time
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from universal_logging import get_logger
from universal_protocol import ErrorCode, error_envelope, get_http_status, is_retryable

from src.scheduling.events import (
    RequestCapacityTimeout,
    RequestCompleted,
    RequestFailed,
    RequestProcessing,
    RequestProfileResolved,
)

# Import from service root to avoid "beyond top-level package" error
from src.schemas.chat_completion import ChatCompletionRequest

from ...core.streaming.wrappers import wrap_streaming_response_for_tracking

if TYPE_CHECKING:
    from ..proxy import StargateProxy

logger = get_logger(__name__)


def _is_capacity_error(exc: HTTPException) -> bool:
    """Return True iff HTTPException indicates retryable capacity/load error.

    Priority: explicit retryable field > metadata-based is_retryable(code).
    The explicit field is authoritative when present (set by our error factories).
    The code-based fallback handles upstream errors without the field.
    """
    if exc.status_code not in (503, 504):
        return False

    detail = exc.detail
    if not isinstance(detail, dict):
        return False

    if "retryable" in detail:
        return detail["retryable"]

    code = detail.get("code", "")
    return is_retryable(code)


def _is_retryable_upstream_error(exc: HTTPException) -> bool:
    """Return True iff HTTPException is a retryable federated upstream error.

    These are HTTP 502 from the executor where the upstream gateway returned
    a transient error (5xx mapped to RESOURCE_UNAVAILABLE with retryable=True).
    """
    if exc.status_code != 502:
        return False

    detail = exc.detail
    if not isinstance(detail, dict):
        return False

    if detail.get("retryable", False):
        return True

    code = detail.get("code", "")
    return is_retryable(code)


def _extract_failed_gateway_id(exc: HTTPException) -> str | None:
    """Extract gateway_id from an upstream error envelope, if present."""
    detail = exc.detail
    if not isinstance(detail, dict):
        return None
    data = detail.get("data")
    if not isinstance(data, dict):
        return None
    gw_id = data.get("gateway_id")
    return gw_id if isinstance(gw_id, str) else None


def _extract_upstream_error_context(exc: HTTPException) -> dict[str, Any]:
    """Extract upstream error details from a federated error envelope.

    Preserves the original upstream status code and message so the
    client-facing error can surface provider-level semantics (e.g. 429).
    """
    detail = exc.detail
    if not isinstance(detail, dict):
        return {}
    data = detail.get("data", {})
    if not isinstance(data, dict):
        data = {}
    ctx: dict[str, Any] = {}
    upstream_status = data.get("status_code")
    if upstream_status is not None:
        ctx["upstream_status_code"] = upstream_status
    msg = detail.get("message")
    if msg:
        ctx["message"] = str(msg)[:300]
    return ctx


def _read_positive_float(
    config: dict[str, object], key: str, default: float, label: str
) -> float:
    """Read a positive float from config, logging ERROR on missing/invalid."""
    raw = config.get(key)
    if raw is None:
        logger.error("%s missing in config; using %.0fs default", label, default)
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.error("Invalid %s=%r; using %.0fs default", label, raw, default)
        return default
    if value <= 0:
        logger.error(
            "Invalid %s=%r (must be > 0); using %.0fs default", label, raw, default
        )
        return default
    return value


def _get_capacity_retry_timeout_s(proxy: StargateProxy) -> float:
    """
    Return total time budget for capacity retries (seconds).

    Source of truth: stargate config `request_queue.queue_timeout`.
    """
    rq = proxy.config.get_request_queue_config()
    return _read_positive_float(
        rq, "queue_timeout", 1800.0, "request_queue.queue_timeout"
    )


def _get_upstream_retry_timeout_s(proxy: StargateProxy) -> float:
    """
    Return time budget for retryable upstream (502) errors (seconds).

    Source of truth: stargate config `request_queue.upstream_retry_timeout`.
    """
    rq = proxy.config.get_request_queue_config()
    return _read_positive_float(
        rq, "upstream_retry_timeout", 120.0, "request_queue.upstream_retry_timeout"
    )


def _retry_timeout_exception(
    *,
    is_capacity: bool,
    model_id: str,
    effective_timeout: float,
    retry_count: int,
    elapsed: float,
    last_exc: HTTPException,
) -> HTTPException:
    """Build the appropriate timeout HTTPException after retry budget exhaustion."""
    data: dict[str, object] = {
        "model_id": model_id,
        "timeout_seconds": effective_timeout,
        "retry_count": retry_count,
        "elapsed_s": round(elapsed, 2),
    }
    if is_capacity:
        message = (
            f"No capacity for model {model_id} after {retry_count} retries "
            f"over {round(elapsed, 1)}s (budget {effective_timeout}s)"
        )
        return HTTPException(
            status_code=get_http_status(ErrorCode.CAPACITY_TIMEOUT),
            detail=error_envelope(
                code=ErrorCode.CAPACITY_TIMEOUT,
                message=message,
                source="master",
                retryable=False,
                data=data,
            ),
        )
    # Upstream: include last error context for diagnostics
    last_detail = last_exc.detail if isinstance(last_exc.detail, dict) else {}
    last_upstream_error = last_detail.get("data")
    data["last_upstream_error"] = (
        last_upstream_error if isinstance(last_upstream_error, dict) else {}
    )
    return HTTPException(
        status_code=502,
        detail=error_envelope(
            code=ErrorCode.RESOURCE_UNAVAILABLE,
            message=f"Upstream retry timeout for model {model_id}",
            source="master",
            retryable=False,
            data=data,
        ),
    )


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
        # Preserve full history before truncation for pipeline context.messages
        request.state.pipeline_full_messages = [
            msg.model_dump() if hasattr(msg, "model_dump") else msg
            for msg in chat_request.messages
        ]

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
        from systems.pipeline.core.dag import PipelineExecutionError
        from systems.pipeline.core.execution.errors import PipelineError

        execution_id: str | None = None
        exec_header: dict[str, str] = {}
        try:
            return await proxy.pipeline_executor.execute(context)
        except PipelineError as exc:
            execution_id = getattr(exc, "execution_id", None)
            exec_header = {"X-Pipeline-Execution-Id": execution_id} if execution_id else {}
            error_dict = exc.to_dict()
            if execution_id:
                error_dict["execution_id"] = execution_id
            logger.error(
                "Pipeline execution failed: %s - %s",
                error_dict.get("error_type"),
                str(exc),
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "error": error_dict,
                    "pipeline_id": context.selected_model,
                },
                headers=exec_header,
            ) from exc
        except PipelineExecutionError as exc:
            execution_id = getattr(exc, "execution_id", None)
            exec_header = {"X-Pipeline-Execution-Id": execution_id} if execution_id else {}
            error_detail: dict[str, object] = {
                "message": f"Internal server error: {exc}",
                "type": "internal_error",
                "code": "internal_server_error",
                "operation": "chat_completions",
            }
            if execution_id:
                error_detail["execution_id"] = execution_id
            logger.error(
                "Pipeline execution error: %s (execution_id=%s)",
                exc,
                execution_id,
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={"error": error_detail},
                headers=exec_header,
            ) from exc

    model_id = str(context.selected_model)
    request_short_id = getattr(context, "request_id", "unknown")[:8]

    start_time = time.time()
    capacity_timeout_s = _get_capacity_retry_timeout_s(proxy)
    upstream_timeout_s = _get_upstream_retry_timeout_s(proxy)
    retry_started = time.monotonic()
    retry_count = 0

    if proxy.event_bus:
        try:
            profile_name = getattr(context, "request_profile", None)
            if profile_name:
                await proxy.event_bus.publish_async_nowait(
                    RequestProfileResolved(
                        request_id=context.request_id,
                        model_id=model_id,
                        profile_name=profile_name,
                    )
                )
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
                is_capacity = _is_capacity_error(exc)
                is_upstream = not is_capacity and _is_retryable_upstream_error(exc)

                if not (is_capacity or is_upstream):
                    raise

                retry_count += 1

                # Upstream failure: exclude the failed gateway so the
                # DecisionEngine routes to a different one on retry.
                # Also clear the stability binding (removes bias).
                if is_upstream:
                    failed_gw = _extract_failed_gateway_id(exc)
                    if failed_gw:
                        context.excluded_gateway_ids.add(failed_gw)
                        upstream_ctx = _extract_upstream_error_context(exc)
                        if upstream_ctx:
                            context.excluded_gateway_errors[failed_gw] = upstream_ctx
                        logger.info(
                            "🚫 [REQ:%s] Excluding gateway %s from routing "
                            "(%d excluded)",
                            request_short_id,
                            failed_gw,
                            len(context.excluded_gateway_ids),
                        )
                    tracker = getattr(proxy, "stability_tracker", None)
                    if tracker is not None:
                        tracker.clear_binding(context.selected_model)

                # Per-request hint is authoritative when present (set by
                # pipeline step timeout or explicit X-Request-Timeout header).
                # For capacity errors, cap at queue_timeout to avoid spinning
                # past the gateway's own queue budget.  For upstream errors
                # the hint is used directly — the caller owns the deadline.
                # Blanket defaults apply only for ad-hoc requests without a hint.
                if context.request_timeout_hint:
                    if is_capacity:
                        effective_timeout = min(
                            context.request_timeout_hint, capacity_timeout_s
                        )
                    else:
                        effective_timeout = context.request_timeout_hint
                elif is_capacity:
                    effective_timeout = capacity_timeout_s
                else:
                    effective_timeout = upstream_timeout_s

                elapsed = time.monotonic() - retry_started
                remaining = effective_timeout - elapsed
                if remaining <= 0:
                    if is_capacity and proxy.event_bus:
                        try:
                            await proxy.event_bus.publish_async_nowait(
                                RequestCapacityTimeout(
                                    request_id=context.request_id,
                                    model_id=model_id,
                                    timeout_seconds=effective_timeout,
                                    retry_count=retry_count,
                                    elapsed_s=round(elapsed, 2),
                                    pipeline_step_id=context.pipeline_step_id,
                                )
                            )
                        except Exception:
                            pass
                    raise _retry_timeout_exception(
                        is_capacity=is_capacity,
                        model_id=model_id,
                        effective_timeout=effective_timeout,
                        retry_count=retry_count,
                        elapsed=elapsed,
                        last_exc=exc,
                    ) from exc

                if await request.is_disconnected():
                    raise asyncio.CancelledError("Client disconnected")

                base_delay = min(2.0, 0.05 * (2 ** min(retry_count - 1, 6)))
                delay_s = min(base_delay * random.uniform(0.5, 1.5), remaining)

                error_kind = "Capacity" if is_capacity else "Upstream"
                log_level = logger.info if retry_count <= 3 else logger.debug
                log_level(
                    "🔄 [REQ:%s] %s retry for %s "
                    "(retry #%d, sleep=%.2fs, budget=%.0fs)",
                    request_short_id,
                    error_kind,
                    model_id,
                    retry_count,
                    delay_s,
                    effective_timeout,
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
