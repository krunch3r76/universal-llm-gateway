"""Federated HTTP error translation for upstream gateway failures.

Translates upstream HTTP status errors into canonical client-facing HTTPException
responses. Preserves cloud-provider 4xx as client-visible 4xx while wrapping
local/federated failures and upstream 5xx as 502 Bad Gateway.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
from fastapi import HTTPException
from universal_logging import get_logger
from universal_protocol import error_envelope

from ..upstream_error import (
    determine_upstream_error_semantics,
    extract_upstream_error_payload,
)

if TYPE_CHECKING:
    from systems.federation.common.types import FederatedGateway

    from ..context import RequestContext

logger = get_logger(__name__)


async def raise_federated_http_error(
    *,
    error: httpx.HTTPStatusError,
    context: RequestContext,
    fed_gateway: FederatedGateway,
    event_bus: Any,
) -> None:
    """Translate an upstream HTTP error into a client-facing HTTPException.

    Performs logging, response snapshots, lifecycle event emission, and error
    envelope construction before raising. Cloud provider 4xx responses are
    preserved as client-visible 4xx; all other failures become 502.

    Args:
        error: The upstream httpx.HTTPStatusError.
        context: Request context (provides request_id, selected_model).
        fed_gateway: Target federated gateway (provides gateway_id, is_cloud).
        event_bus: Event bus for lifecycle event emission (may be None).

    Raises:
        HTTPException: Always raised with canonical error envelope detail.
    """
    from systems.proxy.core.lifecycle import emit_execution_failed

    from ....debug.request_snapshots import write_response_snapshot

    upstream_status_code = int(error.response.status_code)
    upstream_payload = extract_upstream_error_payload(error.response)
    error_code, retryable, response_status_code = determine_upstream_error_semantics(
        upstream_status_code, upstream_payload, is_cloud=fed_gateway.is_cloud
    )

    logger.error(
        "Federated request HTTP error: %d for %s (proxy_status=%d)",
        upstream_status_code,
        fed_gateway.gateway_id,
        response_status_code,
        extra={
            "request_id": context.request_id,
            "gateway_id": fed_gateway.gateway_id,
            "upstream_error": upstream_payload,
            "is_cloud": fed_gateway.is_cloud,
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

    common_message_suffix = f" (gateway_id={fed_gateway.gateway_id})"
    if 400 <= response_status_code < 500:
        message = (
            f"Remote provider rejected request: "
            f"{upstream_status_code}{common_message_suffix}"
        )
    else:
        message = (
            f"Remote gateway error: {upstream_status_code}{common_message_suffix}"
        )

    detail = error_envelope(
        code=error_code,
        message=message,
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
    raise HTTPException(status_code=response_status_code, detail=detail)
