"""
Federated streaming execution: SSE stream forwarding for federated requests.

Part of the `nonstreaming/executor` subpackage. Handles the streaming path
of federated forwarding, selecting between the MasterRequestTracker (atomic
capacity) and direct forwarder (Edge/Remote) based on mode.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
from fastapi import HTTPException
from fastapi.responses import Response
from universal_logging import get_logger
from universal_protocol import ErrorCode, error_envelope

if TYPE_CHECKING:
    from systems.federation.common.config.schema import EndpointCategory
    from systems.federation.common.types import FederatedGateway

    from ..context import RequestContext

logger = get_logger(__name__)


async def _execute_federated_streaming(
    context: RequestContext,
    fed_gateway: FederatedGateway,
    request_body: dict[str, Any],
    request_id: str,
    hop_count: int,
    endpoint_category: EndpointCategory,
    hints: dict[str, Any] | None,
    federation_integration: Any,
    federation_forwarder: Any,
    release_capacity_token: Any,
) -> Response:
    """
    Forward a streaming request to a federated gateway and return an SSE response.

    Selects the forwarding path:
    - Master mode: `request_tracker.forward_stream()` (atomic capacity tracking)
    - Edge/Remote: `federation_forwarder.forward_request_stream()` (direct)

    Converts Remote NDJSON → client-facing SSE via ChunkProcessor.

    Args:
        context: Request context.
        fed_gateway: Target federated gateway.
        request_body: Prepared request dict.
        request_id: Unique request identifier.
        hop_count: Current hop depth.
        endpoint_category: Endpoint type for capacity tracking consistency.
        hints: Optional forwarding hints.
        federation_integration: Provides `.request_tracker` in Master mode.
        federation_forwarder: Direct HTTP forwarder fallback.
        release_capacity_token: Async callable `(context)` to free the slot.

    Returns:
        TrackedStreamingResponse emitting SSE chunks.

    Invariants:
        - yielded_count == 0 ⟹ emit SSE error event + [DONE]
        - yielded_count > 0 ⟹ log and terminate (no error injection)
        - Capacity cleanup: `release_capacity_token` called in finally block
    """
    from ....utils.analysis_section_filter import create_content_filter
    from ...common import ChunkProcessor, ErrorNormalizer
    from ...streaming.error_handler import StreamingErrorHandler
    from ...streaming.response_tracker import TrackedStreamingResponse

    model_name = str(context.selected_model)
    content_filter = create_content_filter(model_name, context.request_id)

    if content_filter:
        logger.info(f"✅ Analysis filter created for federated streaming: {model_name}")

    async def stream_generator_with_cleanup():
        """Convert Remote NDJSON stream → client SSE stream + cleanup slot."""
        chunk_processor = ChunkProcessor(content_filter=content_filter)
        received_count = 0
        yielded_count = 0
        try:
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
                stream = federation_forwarder.forward_request_stream(
                    fed_gateway, request_body, hop_count, request_id, hints=hints
                )
            else:
                stream = request_tracker.forward_stream(
                    gateway=fed_gateway,
                    request_body=request_body,
                    hop_count=hop_count,
                    endpoint_category=endpoint_category,
                    model_id=context.selected_model.routing_key,
                    hints=hints,
                    request_id=request_id,
                    cancel_group=getattr(context, "cancel_group", None),
                )

            async for chunk in stream:
                received_count += 1
                processed = chunk_processor.process_chunk(chunk, context=context)
                if processed is None:
                    continue
                if processed.should_yield and processed.sse_format:
                    yielded_count += 1
                    yield processed.sse_format
                if processed.is_done:
                    break

            if yielded_count == 0:
                logger.error(
                    f"❌ [FED:{request_id[:8]}] Stream completed with ZERO yielded "
                    f"chunks (received={received_count}) for {model_name} on "
                    f"{fed_gateway.gateway_id}"
                )
                yield StreamingErrorHandler.create_sse_error_event(
                    {
                        "error": {
                            "message": (
                                f"Gateway '{fed_gateway.gateway_id}' returned an "
                                "empty stream — no content was generated"
                            ),
                            "type": "gateway_error",
                            "code": "empty_stream",
                        }
                    }
                )
                yield StreamingErrorHandler.create_sse_done_event()
            else:
                logger.info(
                    f"✅ [FED:{request_id[:8]}] Stream completed "
                    f"(received={received_count}, yielded={yielded_count}) "
                    f"for {model_name}"
                )

        except httpx.HTTPStatusError as e:
            logger.exception(
                f"Federated streaming HTTP error "
                f"(received={received_count}, yielded={yielded_count})",
                extra={
                    "request_id": request_id,
                    "gateway_id": fed_gateway.gateway_id,
                    "status_code": e.response.status_code,
                },
            )
            if yielded_count == 0:
                _, error_dict = ErrorNormalizer.normalize_to_openai_format(
                    error=e,
                    default_status=e.response.status_code,
                    operation="federated_streaming",
                    gateway_name=fed_gateway.gateway_id,
                )
                yield StreamingErrorHandler.create_sse_error_event(error_dict)
                yield StreamingErrorHandler.create_sse_done_event()
        except Exception as e:
            logger.exception(
                f"Federated streaming error "
                f"(received={received_count}, yielded={yielded_count})",
                extra={"request_id": request_id},
            )
            if yielded_count == 0:
                _, error_dict = ErrorNormalizer.normalize_to_openai_format(
                    error=e,
                    default_status=503,
                    operation="federated_streaming",
                    gateway_name=fed_gateway.gateway_id,
                )
                yield StreamingErrorHandler.create_sse_error_event(error_dict)
                yield StreamingErrorHandler.create_sse_done_event()
        finally:
            await release_capacity_token(context)

    return TrackedStreamingResponse(
        stream_generator_with_cleanup(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        request_id=request_id,
        model=str(context.selected_model),
    )
