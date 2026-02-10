"""
Bypass mode execution for request executor.

Bypasses all transformations and forwards request directly to gateway.
"""

import json
import time
from typing import TYPE_CHECKING, Any

from fastapi.responses import Response, StreamingResponse
from universal_logging import get_logger

from ...utils.request_context import ForwardContext
from .response_transform import (
    get_response_data,
    transform_dict_to_prompt_format,
    transform_response_to_prompt_format,
)

if TYPE_CHECKING:
    from .context import RequestContext

logger = get_logger(__name__)


async def execute_bypass_mode(
    context: "RequestContext",
    gateway_url: str,
    monitor,
    forward_request_func,
    forward_streaming_request_func,
    model_metadata: dict[str, Any],
) -> Response:
    """
    Execute request in bypass mode.

    Bypasses all transformations and forwards request directly to gateway.

    Args:
        context: Request context with prepared data
        gateway_url: Gateway URL for monitoring
        monitor: Monitoring instance
        forward_request_func: Function to forward non-streaming requests
        forward_streaming_request_func: Function to forward streaming requests
        model_metadata: Model metadata for monitoring

    Returns:
        Response from gateway
    """
    logger.debug(f"BYPASS MODE: Forwarding request for model {context.selected_model}")

    # Emit pre_processing event for GUI consistency
    # In bypass mode, modified_request is same as original_request
    await monitor.log_pre_processing(
        original_request=context.original_request,
        modified_request=context.original_request,
        middleware_actions=context.middleware_actions,
        processing_time_ms=(time.time() - context.start_time) * 1000,
        gateway_endpoint=gateway_url,
        request_id=context.request_id,
        token_metrics=None,
        model_metadata=model_metadata,
    )

    outgoing_request = dict(context.original_request)

    # Write after-transformation snapshot if debugging enabled
    from ...debug.request_snapshots import write_request_snapshot

    await write_request_snapshot(outgoing_request, context.request_id, stage="after")

    request_body = json.dumps(outgoing_request).encode("utf-8")
    headers = dict(context.http_request.headers)
    headers["content-type"] = "application/json"

    if context.client_wants_streaming:
        bypass_context = ForwardContext(
            request_id=context.request_id,
            model_name=str(context.selected_model),
            metadata={"bypass_mode": True},
            middleware_actions=context.middleware_actions,
        )
        response = await forward_streaming_request_func(
            method="POST",
            path="/v1/chat/completions",
            headers=headers,
            content=request_body,
            params=dict(context.http_request.query_params),
            context=bypass_context,
        )
    else:
        bypass_context = ForwardContext(
            request_id=context.request_id,
            model_name=str(context.selected_model),
            metadata={"bypass_mode": True},
            middleware_actions=context.middleware_actions,
        )
        response = await forward_request_func(
            method="POST",
            path="/v1/chat/completions",
            headers=headers,
            content=request_body,
            params=dict(context.http_request.query_params),
            context=bypass_context,
        )

    # Apply response transformation if needed
    if (
        context.chat_request
        and context.chat_request.prompt
        and not isinstance(response, StreamingResponse)
    ):
        response = transform_response_to_prompt_format(response, context)

    # Log monitoring data
    monitoring_response_data: dict[str, Any] = {"bypass_mode": True}
    if (
        context.chat_request
        and context.chat_request.prompt
        and not isinstance(response, StreamingResponse)
    ):
        response_data = get_response_data(response)
        if response_data:
            transformed = transform_dict_to_prompt_format(response_data)
            if transformed:
                monitoring_response_data = transformed

    await monitor.log_chat_completion(
        original_request=context.original_request,
        modified_request=context.original_request,
        middleware_actions=context.middleware_actions,
        processing_time_ms=(time.time() - context.start_time) * 1000,
        gateway_endpoint=gateway_url,
        request_id=context.request_id,
        token_metrics=None,
        model_metadata={"bypass_mode": True},
        response_data=monitoring_response_data,
    )

    return response
