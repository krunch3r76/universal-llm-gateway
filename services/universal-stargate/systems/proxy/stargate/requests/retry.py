"""Unified retry loop for chat completion requests.

Implements capacity-retry and upstream-retry with independent time budgets,
exponential backoff, gateway exclusion on upstream failures, and client
disconnection detection.  Extracted from chat.py so the dispatch hub
(chat.py) is pure orchestration and this module owns all retry semantics.

Error classification helpers:
- ``_is_capacity_error``: 503/504 with retryable envelope or retryable ErrorCode
- ``_is_retryable_upstream_error``: 502 from federated gateway, retryable
- ``_is_all_gateways_excluded``: 503 RESOURCE_UNAVAILABLE with excluded_gateways
  data — all gateways tried and failed; clears exclusion set and retries with
  longer backoff (model may be loading), bounded by upstream_retry_timeout
- Non-retryable: immediate re-raise (permanent resource failures where
  ``can_fit_with_eviction`` is NOT in the failed constraint set)

Events emitted (PascalCase ``@event_factory`` signals):
- ``RequestCompleted`` / ``RequestFailed`` — terminal lifecycle events
- ``RequestCapacityTimeout`` — capacity retry budget exhausted
- ``RequestSnapshotCompleted`` / ``RequestSnapshotFailed`` — response snapshots

Capacity slot release is structural via ``CapacityToken.__aexit__``, not
event-driven.  These events are observational — they do not trigger release.
"""

from __future__ import annotations

import asyncio
import json
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
    RequestSnapshotCompleted,
    RequestSnapshotFailed,
)

from ...core.nonstreaming.context import RequestContext
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


def _is_all_gateways_excluded(exc: HTTPException) -> bool:
    """Return True iff HTTPException indicates all gateways were excluded.

    Raised by ``raise_all_gateways_excluded_error`` when every gateway with the
    model returned upstream errors during a single request.  The error is marked
    ``retryable=False`` because re-routing with the *same* exclusion set is
    pointless — but clearing exclusions and retrying after a backoff is correct:
    the model may not be loaded yet and will become available shortly.
    """
    if exc.status_code != get_http_status(ErrorCode.RESOURCE_UNAVAILABLE):
        return False
    detail = exc.detail
    if not isinstance(detail, dict):
        return False
    data = detail.get("data")
    if not isinstance(data, dict):
        return False
    return "excluded_gateways" in data


def _extract_failed_gateway_id(exc: HTTPException) -> str | None:
    """Extract gateway_id from an upstream error envelope, if present.

    Used by the retry loop to exclude the failed gateway from subsequent
    routing attempts, so the DecisionEngine routes to a different one.
    Returns None if the envelope lacks a structured data.gateway_id field.
    """
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
    """Read a positive float from *config*, logging ERROR on missing or invalid values.

    Returns *default* with an ERROR log when the key is absent, non-numeric,
    or non-positive.  Follows the defaults policy: every use of a default is
    logged at ERROR level so silent misconfiguration is observable.
    """
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
    elapsed_s: float,
    last_exc: HTTPException,
) -> HTTPException:
    """Build the appropriate timeout HTTPException after retry budget exhaustion.

    Capacity timeouts use ErrorCode.CAPACITY_TIMEOUT (non-retryable).
    Upstream timeouts use ErrorCode.RESOURCE_UNAVAILABLE with the last
    upstream error context preserved for client-facing diagnostics.
    Both include retry_count, elapsed_s, and effective timeout in the envelope.
    """
    data: dict[str, object] = {
        "model_id": model_id,
        "timeout_seconds": effective_timeout,
        "retry_count": retry_count,
        "elapsed_s": round(elapsed_s, 2),
    }
    if is_capacity:
        message = (
            f"No capacity for model {model_id} after {retry_count} retries "
            f"over {round(elapsed_s, 1)}s (budget {effective_timeout}s)"
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


async def execute_with_retry(
    proxy: StargateProxy,
    context: RequestContext,
    model_id: str,
    request: Request,
    start_time: float,
) -> Response:
    """Execute a chat completion request with unified capacity and upstream retry.

    Delegates to proxy.request_executor.execute_request in a retry loop that
    handles two independent failure classes:

    1. Capacity errors (503/504, classified by ``_is_capacity_error``): retried
       up to ``queue_timeout`` with exponential backoff.  On budget exhaustion,
       emits ``RequestCapacityTimeout``.
    2. Upstream errors (502, classified by ``_is_retryable_upstream_error``):
       retried up to ``upstream_retry_timeout``, excluding the failed gateway
       (via ``_extract_failed_gateway_id``) from subsequent routing.

    Non-retryable errors (including permanent resource failures where
    ``can_fit_with_eviction`` is NOT in the failed constraint set) are
    re-raised immediately without retry.

    Terminal events emitted (exactly one set per request):
      Success: ``RequestCompleted`` + ``RequestSnapshotCompleted``
      Failure: ``RequestFailed`` + ``RequestSnapshotFailed``
      Capacity timeout: ``RequestCapacityTimeout`` (before ``RequestFailed``)
    Streaming responses emit terminal events on stream completion via
    ``wrap_streaming_response_for_tracking``.

    INVARIANT: every exit path (success, retry exhaustion, unhandled exception,
    client disconnect) emits exactly one terminal event set.  Capacity slot
    release is structural (``CapacityToken.__aexit__``), not event-driven.
    """
    request_short_id = getattr(context, "request_id", "unknown")[:8]

    capacity_timeout_s = _get_capacity_retry_timeout_s(proxy)
    upstream_timeout_s = _get_upstream_retry_timeout_s(proxy)
    retry_started = time.monotonic()
    retry_count = 0

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

                duration = time.time() - start_time
                if proxy.event_bus:
                    try:
                        await proxy.event_bus.publish_async_nowait(
                            RequestCompleted(
                                request_id=context.request_id,
                                gateway_url=proxy.gateway_url,
                                model_id=model_id,
                                duration=duration,
                            )
                        )
                        resp_body = getattr(response, "body", b"")
                        if isinstance(resp_body, bytes):
                            try:
                                resp_data = json.loads(resp_body)
                                choices = resp_data.get("choices", [])
                                content = (
                                    choices[0].get("message", {}).get("content", "")
                                    if choices
                                    else ""
                                )
                                await proxy.event_bus.publish_async_nowait(
                                    RequestSnapshotCompleted(
                                        request_id=context.request_id,
                                        model_id=model_id,
                                        gateway_id=getattr(
                                            context, "target_gateway_id", ""
                                        )
                                        or "",
                                        content=content,
                                        usage=resp_data.get("usage"),
                                        duration_s=duration,
                                    )
                                )
                            except Exception as exc:
                                logger.warning(
                                    "Failed to emit RequestSnapshotCompleted "
                                    "for %s (%s): %s",
                                    context.request_id,
                                    type(exc).__name__,
                                    exc,
                                    exc_info=True,
                                )
                    except Exception as exc:  # pragma: no cover - defensive logging
                        logger.warning(
                            "Failed to emit REQUEST_COMPLETED event: %s",
                            exc,
                            exc_info=True,
                        )

                return response

            except HTTPException as exc:
                is_capacity = _is_capacity_error(exc)
                is_upstream = not is_capacity and _is_retryable_upstream_error(exc)
                is_all_excluded = (
                    not is_capacity
                    and not is_upstream
                    and _is_all_gateways_excluded(exc)
                )

                if not (is_capacity or is_upstream or is_all_excluded):
                    raise

                retry_count += 1

                if is_upstream:
                    # Upstream failure: exclude the failed gateway so the
                    # DecisionEngine routes to a different one on retry.
                    # Also clear the stability binding (removes bias).
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
                elif is_all_excluded:
                    # All gateways failed — model may not be loaded yet.
                    # Clear exclusions so routing can try all gateways again
                    # after a backoff.  Bounded by upstream_retry_timeout.
                    logger.info(
                        "🔄 [REQ:%s] All gateways excluded for %s; "
                        "clearing %d exclusions for re-queue (retry #%d)",
                        request_short_id,
                        model_id,
                        len(context.excluded_gateway_ids),
                        retry_count,
                    )
                    context.excluded_gateway_ids.clear()
                    context.excluded_gateway_errors.clear()
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
                        effective_timeout = min(
                            context.request_timeout_hint, upstream_timeout_s
                        )
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
                        except Exception as exc:
                            logger.warning(
                                "Failed to emit RequestCapacityTimeout for %s: %s",
                                context.request_id,
                                exc,
                            )
                    raise _retry_timeout_exception(
                        is_capacity=is_capacity,
                        model_id=model_id,
                        effective_timeout=effective_timeout,
                        retry_count=retry_count,
                        elapsed_s=elapsed,
                        last_exc=exc,
                    ) from exc

                if await request.is_disconnected():
                    raise asyncio.CancelledError("Client disconnected")

                # All-excluded uses longer backoff: model may need time to
                # load (~10-30s).  2s → 4s → 8s → 10s(cap).
                if is_all_excluded:
                    base_delay = min(10.0, 2.0 * (2 ** min(retry_count - 1, 3)))
                else:
                    base_delay = min(2.0, 0.05 * (2 ** min(retry_count - 1, 6)))
                delay_s = min(base_delay * random.uniform(0.5, 1.5), remaining)

                error_kind = (
                    "Capacity"
                    if is_capacity
                    else "All-excluded"
                    if is_all_excluded
                    else "Upstream"
                )
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
            exc_info=True,
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
                error_code = None
                if isinstance(exc, HTTPException) and isinstance(exc.detail, dict):
                    error_code = exc.detail.get("code")
                await proxy.event_bus.publish_async_nowait(
                    RequestSnapshotFailed(
                        request_id=context.request_id,
                        model_id=model_id,
                        error=str(exc),
                        error_code=error_code,
                    )
                )
            except Exception as emit_err:  # pragma: no cover - defensive logging
                logger.warning(
                    "Failed to emit REQUEST_FAILED event: %s",
                    emit_err,
                    exc_info=True,
                )
        raise
