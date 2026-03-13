"""
Federated non-streaming execution: tracker/forwarder dispatch + JSONResponse assembly.

Part of the `nonstreaming/executor` subpackage. Contains two functions:
- `_forward_via_tracker_or_forwarder`: raw forwarding (Master tracker or direct HTTP)
- `_execute_federated_nonstreaming`: full non-streaming pipeline (forward, filter,
  snapshot, lifecycle events)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
from fastapi import HTTPException
from fastapi.responses import JSONResponse, Response
from universal_logging import get_logger
from universal_protocol import ErrorCode, error_envelope

from ..response_helpers import (
    apply_content_filter_to_response,
    extract_remote_headers,
    prepare_federation_headers,
)
from ..upstream_error import (
    extract_upstream_error_payload,
    map_upstream_status_to_error_code,
)

if TYPE_CHECKING:
    from systems.federation.common.config.schema import EndpointCategory
    from systems.federation.common.types import FederatedGateway

    from ..context import RequestContext

logger = get_logger(__name__)


async def _forward_via_tracker_or_forwarder(
    fed_gateway: FederatedGateway,
    request_body: dict[str, Any],
    hop_count: int,
    request_id: str,
    endpoint_category: EndpointCategory,
    model_id: str,
    hints: dict[str, Any] | None,
    federation_integration: Any,
    federation_forwarder: Any,
    context: RequestContext | None = None,
) -> tuple[Any, dict[str, str], int]:
    """
    Forward a non-streaming request via the tracker (Master) or forwarder (Edge).

    Master mode uses `request_tracker.forward()` for atomic capacity accounting.
    Edge/Remote mode falls back to the direct `federation_forwarder`.

    Returns:
        Tuple of (response_content, response_headers, status_code).
    """
    request_tracker = None
    if federation_integration is not None:
        request_tracker = federation_integration.request_tracker

    if request_tracker is None:
        if federation_forwarder is None:
            raise HTTPException(
                status_code=503,
                detail=error_envelope(
                    code=ErrorCode.CONFIGURATION_ERROR,
                    message=(
                        "Federation forwarder not available "
                        "(required for federated forwarding)"
                    ),
                    source="master",
                    retryable=False,
                    data={},
                ),
            )
        response = await federation_forwarder.forward_request(
            fed_gateway, request_body, hop_count, request_id, hints=hints
        )
        return response.json(), extract_remote_headers(response), response.status_code

    cancel_group = getattr(context, "cancel_group", None) if context else None
    response_content = await request_tracker.forward(
        gateway=fed_gateway,
        request_body=request_body,
        hop_count=hop_count,
        endpoint_category=endpoint_category,
        model_id=model_id,
        hints=hints,
        request_id=request_id,
        cancel_group=cancel_group,
    )
    # Tracker path does not preserve remote headers (informational only in Master mode).
    return response_content, {}, 200


async def _forward_or_recover(
    *,
    forward_fn: Any,
    fed_gateway: FederatedGateway,
    model_id: Any,
    federated_manager: Any,
    federation_forwarder: Any,
    request_tracker: Any,
    event_bus: Any,
    request_id: str,
) -> tuple[Any, dict[str, str], int]:
    """Forward with one OOM recovery attempt on 500.

    Returns (content, headers, status_code) on success or non-recoverable error.
    Raises httpx.HTTPStatusError for non-500 HTTP errors.
    Raises httpx.TimeoutException on timeout.
    """
    from .oom_recovery import attempt_oom_recovery

    # First attempt
    first_error: httpx.HTTPStatusError | None = None
    try:
        content, headers, status = await forward_fn()
    except httpx.HTTPStatusError as e:
        if e.response.status_code != 500:
            raise
        content, headers, status = (
            extract_upstream_error_payload(e.response),
            {},
            500,
        )
        first_error = e

    if status != 500:
        return content, headers, status

    # 500 detected — attempt OOM recovery
    recovered = await attempt_oom_recovery(
        gateway=fed_gateway,
        model_id=model_id,
        federated_manager=federated_manager,
        federation_forwarder=federation_forwarder,
        request_tracker=request_tracker,
        event_bus=event_bus,
        request_id=request_id,
    )

    if not recovered:
        if first_error is not None:
            raise first_error
        return content, headers, status

    # Retry after eviction
    try:
        content, headers, status = await forward_fn()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 500:
            _ban_and_emit(
                federated_manager=federated_manager,
                model_id=model_id,
                gateway_id=fed_gateway.gateway_id,
                event_bus=event_bus,
                request_id=request_id,
            )
        raise

    if status == 500:
        _ban_and_emit(
            federated_manager=federated_manager,
            model_id=model_id,
            gateway_id=fed_gateway.gateway_id,
            event_bus=event_bus,
            request_id=request_id,
        )
    elif event_bus:
        from src.scheduling.events.routing import OomRecoverySucceeded

        event_bus.publish_async_nowait(
            OomRecoverySucceeded(
                request_id=request_id,
                model_id=model_id.routing_key,
                gateway_id=fed_gateway.gateway_id,
                evicted_count=0,
            )
        )

    return content, headers, status


def _ban_and_emit(
    *,
    federated_manager: Any,
    model_id: Any,
    gateway_id: str,
    event_bus: Any,
    request_id: str,
) -> None:
    """Ban model on gateway and emit failure + ban events."""
    from src.scheduling.events.routing import OomInferenceBanned, OomRecoveryFailed

    federated_manager.mark_inference_banned(gateway_id, model_id)
    if event_bus:
        event_bus.publish_async_nowait(
            OomRecoveryFailed(
                request_id=request_id,
                model_id=model_id.routing_key,
                gateway_id=gateway_id,
            )
        )
        event_bus.publish_async_nowait(
            OomInferenceBanned(
                model_id=model_id.routing_key,
                gateway_id=gateway_id,
            )
        )


async def _execute_federated_nonstreaming(
    context: RequestContext,
    fed_gateway: FederatedGateway,
    request_body: dict[str, Any],
    request_id: str,
    hop_count: int,
    endpoint_category: EndpointCategory,
    hints: dict[str, Any] | None,
    federation_integration: Any,
    federation_forwarder: Any,
    event_bus: Any,
    *,
    federated_manager: Any | None = None,
) -> Response:
    """
    Forward a non-streaming request and return a JSONResponse.

    Pipeline: forward → snapshot → content filter → federation headers
    → snapshot → lifecycle events.

    Error semantics:
        - 4xx/5xx from remote → 502 Bad Gateway
        - Timeout → 504 Gateway Timeout

    Invariant:
        ∀ success_response: status_code = remote_status_code
        ∧ headers preserved with x-federated- namespace
    """
    from systems.proxy.core.lifecycle import (
        emit_execution_completed,
        emit_execution_failed,
    )

    from ....debug.request_snapshots import write_response_snapshot
    from ....utils.analysis_section_filter import create_content_filter

    model_name = str(context.selected_model)
    content_filter = create_content_filter(model_name, context.request_id)

    if content_filter:
        logger.info(
            f"✅ Analysis filter created for federated non-streaming: {model_name}"
        )

    try:
        request_tracker = (
            federation_integration.request_tracker
            if federation_integration is not None
            else None
        )

        async def _do_forward():
            return await _forward_via_tracker_or_forwarder(
                fed_gateway,
                request_body,
                hop_count,
                request_id,
                endpoint_category,
                str(context.selected_model),
                hints,
                federation_integration,
                federation_forwarder,
                context,
            )

        if federated_manager is not None:
            (
                response_content,
                response_headers,
                response_status_code,
            ) = await _forward_or_recover(
                forward_fn=_do_forward,
                fed_gateway=fed_gateway,
                model_id=context.selected_model,
                federated_manager=federated_manager,
                federation_forwarder=federation_forwarder,
                request_tracker=request_tracker,
                event_bus=event_bus,
                request_id=request_id,
            )
        else:
            (
                response_content,
                response_headers,
                response_status_code,
            ) = await _do_forward()

        await write_response_snapshot(
            response_content, context.request_id, stage="response-from-gateway"
        )

        response_content = apply_content_filter_to_response(
            response_content=response_content,
            content_filter=content_filter,
            model_name=model_name,
            logger=logger,
        )

        final_headers = prepare_federation_headers(
            fed_gateway=fed_gateway,
            base_headers=response_headers,
        )

        await write_response_snapshot(
            response_content, context.request_id, stage="response-to-client"
        )

        await emit_execution_completed(
            event_bus=event_bus,
            url=fed_gateway.remote_stargate_url,
            model_id=context.selected_model.routing_key,
            request_id=context.request_id,
            gateway_id=fed_gateway.gateway_id,
        )

        return JSONResponse(
            content=response_content,
            status_code=response_status_code,
            headers=final_headers,
        )

    except httpx.HTTPStatusError as e:
        upstream_status_code = int(e.response.status_code)
        upstream_payload = extract_upstream_error_payload(e.response)
        error_code, retryable = map_upstream_status_to_error_code(
            upstream_status_code, upstream_payload
        )

        logger.error(
            "Federated request HTTP error: %d for %s",
            upstream_status_code,
            fed_gateway.gateway_id,
            extra={
                "request_id": context.request_id,
                "gateway_id": fed_gateway.gateway_id,
                "upstream_error": upstream_payload,
            },
        )

        await write_response_snapshot(
            upstream_payload, context.request_id, stage="response-from-gateway"
        )
        await emit_execution_failed(
            event_bus=event_bus,
            url=fed_gateway.remote_stargate_url,
            model_id=context.selected_model.routing_key,
            request_id=context.request_id,
            gateway_id=fed_gateway.gateway_id,
            error=f"Upstream {upstream_status_code}",
        )

        detail = error_envelope(
            code=error_code,
            message=(
                f"Remote gateway error: {upstream_status_code} "
                f"(gateway_id={fed_gateway.gateway_id})"
            ),
            source="master",
            retryable=retryable,
            data={
                "status_code": upstream_status_code,
                "gateway_id": fed_gateway.gateway_id,
                "upstream_error": upstream_payload,
            },
        )

        await write_response_snapshot(
            detail, context.request_id, stage="response-to-client"
        )
        raise HTTPException(status_code=502, detail=detail)

    except httpx.TimeoutException:
        logger.error(f"Federated request timeout for {fed_gateway.gateway_id}")
        await emit_execution_failed(
            event_bus=event_bus,
            url=fed_gateway.remote_stargate_url,
            model_id=context.selected_model.routing_key,
            request_id=context.request_id,
            gateway_id=fed_gateway.gateway_id,
            error="Gateway timeout",
        )
        raise HTTPException(
            status_code=504,
            detail=error_envelope(
                code=ErrorCode.REQUEST_TIMEOUT,
                message="Remote gateway timeout",
                source="master",
                retryable=True,
                data={"gateway_id": fed_gateway.gateway_id},
            ),
        )

    except Exception as e:
        logger.exception(f"Federated request error: {e}")
        await emit_execution_failed(
            event_bus=event_bus,
            url=fed_gateway.remote_stargate_url,
            model_id=context.selected_model.routing_key,
            request_id=context.request_id,
            gateway_id=fed_gateway.gateway_id,
            error=str(e),
        )
        raise HTTPException(
            status_code=502,
            detail=error_envelope(
                code=ErrorCode.UNEXPECTED_ERROR,
                message=f"Federated gateway error: {e}",
                source="master",
                retryable=True,
                data={"gateway_id": fed_gateway.gateway_id},
            ),
        )
