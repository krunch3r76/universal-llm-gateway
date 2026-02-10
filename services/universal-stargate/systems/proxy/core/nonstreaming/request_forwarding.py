"""
Request forwarding with automatic slot cleanup.

Handles the complete request forwarding lifecycle including:
- Model metadata fetching
- Pre-processing logging
- Gateway forwarding
- Response transformation
- Monitoring

Automatically releases slots on exception if slot is reserved.
"""

import json
import time
from typing import TYPE_CHECKING

from fastapi.responses import Response, StreamingResponse
from universal_logging import format_json_for_log, get_logger

from .forwarding import forward_to_gateway
from .response_transform import (
    get_response_data,
    transform_dict_to_prompt_format,
    transform_response_to_prompt_format,
)

if TYPE_CHECKING:
    from .context import RequestContext

logger = get_logger(__name__)


async def forward_request_with_cleanup(
    context: "RequestContext",
    gateway_name: str | None,
    gateway_url: str,
    slot_reserved: bool,
    monitor,
    forward_request_func,
    forward_streaming_request_func,
    get_model_metadata_func,
) -> Response:
    """
    Forward request to gateway with event-driven cleanup.

    Handles the complete request lifecycle:
    1. Fetch model metadata (non-blocking, cached)
    2. Log pre-processing metrics
    3. Forward request to gateway
    4. Transform response (if needed)
    5. Log completion metrics

    Cleanup behavior:
    - Slot release is event-driven via MODEL_EXECUTION_COMPLETED
    - Caller must publish event on both success and error paths
    - Streaming wrapper publishes event on stream completion

    Args:
        context: Request context
        gateway_name: Target gateway name
        gateway_url: Gateway base URL
        slot_reserved: Whether caller holds a slot (unused, kept for BC)
        monitor: Monitoring service
        forward_request_func: Non-streaming forward function
        forward_streaming_request_func: Streaming forward function
        get_model_metadata_func: Metadata fetch function

    Returns:
        Response from gateway (streaming or non-streaming)

    Raises:
        Exception: Any error during forwarding

    Invariant:
        ∀ request: caller publishes MODEL_EXECUTION_COMPLETED on success/error
        ∀ StreamingResponse: wrapper publishes event on stream completion
    """
    try:
        logger.info(
            "✅ TOKEN MANAGEMENT COMPLETE - proceeding to metadata fetch and forwarding"
        )

        # Can pass ModelId directly (normalized comparison handled internally)
        model_metadata = await get_model_metadata_func(context.selected_model)

        logger.info("✅ METADATA FETCH COMPLETE - proceeding to pre-processing log")

        await monitor.log_pre_processing(
            original_request=context.original_request,
            modified_request=context.modified_request,
            middleware_actions=context.middleware_actions,
            processing_time_ms=(time.time() - context.start_time) * 1000,
            gateway_endpoint=gateway_url,
            request_id=context.request_id,
            token_metrics=context.token_metrics.dict()
            if context.token_metrics
            else None,
            model_metadata=model_metadata,
        )

        base_request = context.modified_request or context.original_request
        outgoing_request = dict(base_request)
        request_body = json.dumps(outgoing_request).encode("utf-8")
        headers = dict(context.http_request.headers) if context.http_request else {}
        headers["content-type"] = "application/json"

        logger.info(
            "📤 REQUEST BODY (to Gateway): %s",
            format_json_for_log(base_request, truncate=False),
        )

        # Write after-modification snapshot if debugging enabled
        # (covers streaming + nonstreaming)
        from ...debug.request_snapshots import write_request_snapshot

        await write_request_snapshot(base_request, context.request_id, stage="after")

        logger.info(
            f"🚀 FORWARDING REQUEST to gateway for model: {context.selected_model}"
        )

        response = await forward_to_gateway(
            context=context,
            headers=headers,
            request_body=request_body,
            model_metadata=model_metadata,
            forward_request_func=forward_request_func,
            forward_streaming_request_func=forward_streaming_request_func,
        )

        # Transfer cleanup ownership for streaming responses
        # Note: Non-streaming responses already cleaned up in forwarding.py
        is_streaming = isinstance(response, StreamingResponse)

        # Apply response transformation if needed (non-streaming only)
        if context.chat_request and context.chat_request.prompt and not is_streaming:
            response = transform_response_to_prompt_format(response, context)

        # Capture response data for monitoring
        response_data = get_response_data(response)
        monitoring_response_data = response_data
        if (
            context.chat_request
            and context.chat_request.prompt
            and not is_streaming
            and response_data
        ):
            transformed = transform_dict_to_prompt_format(response_data)
            if transformed:
                monitoring_response_data = transformed

        await monitor.log_chat_completion(
            original_request=context.original_request,
            modified_request=context.modified_request,
            middleware_actions=context.middleware_actions,
            processing_time_ms=(time.time() - context.start_time) * 1000,
            gateway_endpoint=gateway_url,
            request_id=context.request_id,
            token_metrics=context.token_metrics.dict()
            if context.token_metrics
            else None,
            model_metadata=model_metadata,
            response_data=monitoring_response_data,
        )

        return response

    except Exception as e:
        # Event-driven release handles cleanup
        # Caller must ensure MODEL_EXECUTION_COMPLETED is published on error path

        logger.error(f"❌ Exception during model metadata/forwarding/processing: {e}")
        raise
