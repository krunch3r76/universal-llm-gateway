"""
Request forwarding to gateway.

Handles streaming and non-streaming request forwarding.
"""

from typing import TYPE_CHECKING

from fastapi import HTTPException
from fastapi.responses import Response
from universal_logging import get_logger

from ...utils.request_context import RequestContextBuilder

if TYPE_CHECKING:
    from .context import RequestContext

logger = get_logger(__name__)


async def forward_to_gateway(
    context: "RequestContext",
    headers: dict,
    request_body: bytes,
    model_metadata: dict,
    forward_request_func,
    forward_streaming_request_func,
) -> Response:
    """
    Forward request to gateway (streaming or non-streaming).

    Slot release is event-driven via MODEL_EXECUTION_COMPLETED.

    Args:
        context: Request context
        headers: HTTP headers
        request_body: Encoded request body
        model_metadata: Model metadata for context
        forward_request_func: Function for non-streaming requests
        forward_streaming_request_func: Function for streaming requests

    Returns:
        Response from gateway

    Raises:
        HTTPException: If forwarding fails
    """
    if context.client_wants_streaming:
        logger.debug(
            f"Forwarding streaming chat completion to gateway for model: "
            f"{context.selected_model}"
        )
        context.middleware_actions.append("forwarding_streaming_request")

        forward_context = RequestContextBuilder.from_request_context(
            context,
            model_metadata,
            gateway_instance=context.selected_gateway_instance,
        )

        if forward_streaming_request_func is None:
            raise HTTPException(
                status_code=500,
                detail="Streaming request handler not initialized",
            )

        return await forward_streaming_request_func(
            method="POST",
            path="/v1/chat/completions",
            headers=headers,
            content=request_body,
            params=dict(context.http_request.query_params),
            context=forward_context,
            request=context.http_request,
        )
    else:
        logger.debug(
            f"Forwarding non-streaming chat completion to gateway for model: "
            f"{context.selected_model}"
        )
        context.middleware_actions.append("forwarding_non_streaming_request")

        forward_context = RequestContextBuilder.from_request_context(
            context,
            model_metadata,
            gateway_instance=context.selected_gateway_instance,
        )

        response = await forward_request_func(
            method="POST",
            path="/v1/chat/completions",
            headers=headers,
            content=request_body,
            params=dict(context.http_request.query_params),
            context=forward_context,
            request=context.http_request,
        )

        # Slot release happens via MODEL_EXECUTION_COMPLETED event
        # (event published by caller after this function returns)

        return response
