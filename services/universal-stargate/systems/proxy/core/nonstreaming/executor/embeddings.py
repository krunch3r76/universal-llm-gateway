"""
Embedding request execution via federated gateways.

Part of the `nonstreaming/executor` subpackage. Extracts the embedding
request flow from `RequestExecutor` as standalone functions, accepting
dependencies explicitly rather than accessing them via `self`.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Protocol

from fastapi import HTTPException
from universal_logging import get_logger
from universal_protocol import ErrorCode, error_envelope

from ..context import RequestContext

logger = get_logger(__name__)

if TYPE_CHECKING:
    from universal_event_bus import EventBus


class _RequestTrackerLike(Protocol):
    async def forward_embedding(
        self,
        *,
        gateway: Any,
        request_body: dict[str, Any],
        model_id: str,
        request_id: str,
    ) -> dict[str, Any]: ...


class _FederationIntegrationLike(Protocol):
    request_tracker: _RequestTrackerLike | None


class _FederationForwarderLike(Protocol):
    async def forward_embedding_request(
        self,
        *,
        gateway: Any,
        request_body: dict[str, Any],
        request_id: str,
    ) -> dict[str, Any]: ...


async def execute_embedding_request(
    model_id: str,
    request_body: dict[str, Any],
    request_id: str | None,
    *,
    select_gateway_fn: Callable[[RequestContext], Awaitable[None]],
    release_routing_key_fn: Callable[[str], None],
    release_capacity_token_fn: Callable[[RequestContext], Awaitable[None]],
    forward_embedding_fn: Callable[..., Awaitable[dict[str, Any]]],
    event_bus: EventBus,
) -> dict[str, Any]:
    """
    Execute an embedding request via federation.

    Constructs a minimal `RequestContext` (bypass, no token counting), routes
    to the appropriate gateway, forwards the request, and emits lifecycle events.

    Args:
        model_id: String model identifier (parsed at this boundary).
        request_body: Embedding request body dict.
        request_id: Optional caller-provided ID; a UUID is generated if absent.
        select_gateway_fn: Async callable `(context)` that selects the gateway.
        release_routing_key_fn: Callable `(request_id)` to free the routing key.
        release_capacity_token_fn: Async callable `(context)` to free the slot.
        forward_embedding_fn: Async callable `(gateway, request_body, request_id)`
            that performs the actual forward and returns the response dict.
        event_bus: Event bus for lifecycle signals.

    Returns:
        Embedding response dict from the gateway.

    Raises:
        HTTPException: 500 if no gateway available, or any forwarding error.
    """
    from model_id import ModelId

    from systems.federation.common.config.schema import EndpointCategory
    from systems.proxy.core.lifecycle import (
        emit_execution_completed,
        emit_execution_failed,
    )

    parsed_model_id = ModelId.parse(model_id)
    resolved_request_id = request_id or str(uuid.uuid4())

    context = RequestContext(
        request_id=resolved_request_id,
        start_time=time.time(),
        selected_model=parsed_model_id,
        original_request=request_body,
        raw_client_fields={},
        user_params={},
        middleware_actions=[],
        bypass_transformations=True,
        disable_profile=True,
        skip_token_counting=True,
        http_request=None,
        chat_request=None,
        selected_gateway=None,
    )
    # CRITICAL: Pre-set endpoint category — http_request is None for programmatic
    # calls so routing cannot derive it; wrong key causes capacity reservation leak.
    context.routing_endpoint_category = EndpointCategory.EMBEDDING
    context.model_sticky = False

    fed_gateway = None
    try:
        await select_gateway_fn(context)
        fed_gateway = context.federated_gateway
        if not fed_gateway:
            raise HTTPException(
                status_code=503,
                detail=error_envelope(
                    code=ErrorCode.RESOURCE_UNAVAILABLE,
                    message=f"No gateway available for model: {model_id}",
                    source="master",
                    retryable=True,
                    data={"model_id": model_id},
                ),
            )

        result = await forward_embedding_fn(
            gateway=fed_gateway,
            request_body=request_body,
            request_id=resolved_request_id,
        )
        await emit_execution_completed(
            event_bus=event_bus,
            url=fed_gateway.remote_stargate_url,
            model_id=context.selected_model.routing_key,
            request_id=resolved_request_id,
            gateway_id=fed_gateway.gateway_id,
        )
        return result
    except Exception as e:
        gateway_url = fed_gateway.remote_stargate_url if fed_gateway else "unknown"
        gateway_id = fed_gateway.gateway_id if fed_gateway else "unknown"
        await emit_execution_failed(
            event_bus=event_bus,
            url=gateway_url,
            model_id=context.selected_model.routing_key,
            request_id=resolved_request_id,
            gateway_id=gateway_id,
            error=str(e),
        )
        raise
    finally:
        release_routing_key_fn(resolved_request_id)
        # Release admission capacity only when a gateway was selected
        # (token was acquired).
        if fed_gateway is not None:
            await release_capacity_token_fn(context)


async def forward_embedding_request(
    gateway: Any,
    request_body: dict[str, Any],
    request_id: str,
    *,
    federation_integration: _FederationIntegrationLike | None,
    federation_forwarder: _FederationForwarderLike | None,
) -> dict[str, Any]:
    """
    Forward an embedding request to a federated gateway.

    Uses the request tracker in Master mode (atomic capacity) or falls back
    to the direct forwarder in Edge/Remote mode.

    Args:
        gateway: Target FederatedGateway.
        request_body: Embedding request dict (must contain "model").
        request_id: Unique request identifier.
        federation_integration: Provides `.request_tracker` in Master mode.
        federation_forwarder: Direct HTTP forwarder fallback.

    Returns:
        Embedding response dict from the gateway.

    Raises:
        HTTPException: 400 if "model" field missing; 503 if no forwarder configured.
    """
    model_id = request_body.get("model")
    if model_id is None:
        raise HTTPException(
            status_code=400,
            detail=error_envelope(
                code=ErrorCode.INVALID_REQUEST,
                message="Missing required field: model",
                source="master",
                retryable=False,
                data={"field": "model"},
            ),
        )

    request_tracker = None
    if federation_integration is not None:
        request_tracker = federation_integration.request_tracker

    if request_tracker is not None:
        return await request_tracker.forward_embedding(
            gateway=gateway,
            request_body=request_body,
            model_id=model_id,
            request_id=request_id,
        )

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

    return await federation_forwarder.forward_embedding_request(
        gateway=gateway,
        request_body=request_body,
        request_id=request_id,
    )
