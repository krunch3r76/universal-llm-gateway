"""Streaming response generation for chat completions.

Responsibility: orchestrate streaming response (event emission, model gating,
worker iteration, exception dispatch). Error classification delegated to error_mapping.
"""

import asyncio

from process_ipc.core.exceptions import ProcessError
from universal_event_bus import EventBus
from universal_logging import get_logger
from universal_protocol.errors import StreamError

from src.core.errors import ErrorCode
from src.core.events.types import RequestInferenceStarted

from ..events import emit_request_queued_nowait
from .error_mapping import (
    iter_process_error_events,
    iter_runtime_error_events,
    iter_timeout_error_events,
)
from .ndjson import (
    convert_worker_chunk_to_openai_format,
    create_ndjson_event,
    iter_error_and_complete_events,
    serialize_ndjson_event,
)

logger = get_logger(__name__)


async def generate_streaming_response(
    worker_controller,
    model_id: str,
    messages,
    event_bus: EventBus,
    correlation_id: str | None = None,
    timeout_hint: float | None = None,
    request_id: str | None = None,
    gateway_url: str = "unknown",
    **kwargs,
):
    """
    Generate streaming response using NDJSON with signal/payload structure.

    Format: application/newline-delimited-json
    Each line: {"signal": "chunk"|"error"|"complete", "payload": ...}

    Args:
        worker_controller: Controller for worker operations
        model_id: Model identifier
        messages: Chat messages
        event_bus: Event bus for publishing events (required)
        correlation_id: Optional correlation ID for tracing
        timeout_hint: Optional timeout hint from upstream (federation/pipeline)
        request_id: Optional request ID from upstream (prefers Stargate-provided identity)
        gateway_url: Gateway URL for telemetry (resolved from request by caller)
        **kwargs: Additional generation parameters

    Yields:
        str: NDJSON-formatted events

    Raises:
        ValueError: If event_bus is None (events are a hard dependency)
    """
    if event_bus is None:
        raise ValueError("event_bus is required for streaming responses")

    if request_id is None:
        import uuid
        request_id = str(uuid.uuid4())

    # Emit REQUEST_QUEUED event
    await emit_request_queued_nowait(
        event_bus, model_id, request_id, messages, kwargs, stream=True
    )

    stream_id = None
    try:
        logger.info(f"Processing streaming inference for model: {model_id}")

        # Ensure model is loaded
        if not await worker_controller.ensure_model_loaded(model_id):
            if not worker_controller.auto_load_on_request:
                for item in iter_error_and_complete_events(
                    f"Model '{model_id}' is not loaded",
                    "invalid_request_error",
                    "model_not_loaded",
                ):
                    yield item
                return
            else:
                raise RuntimeError(f"Failed to load model {model_id}")

        # Request-scoped runtime-start boundary (execution handoff begins here)
        await event_bus.publish_nowait(
            RequestInferenceStarted(
                request_id=request_id,
                model_id=model_id,
                gateway_url=gateway_url,
                correlation_id=correlation_id,
            )
        )

        logger.info(f"🚀 [REQ:{request_id[:8]}] Starting stream for {model_id}")
        chunk_count = 0

        async for chunk in worker_controller.generate_chat_completion_stream(
            model_id=model_id,
            messages=messages,
            correlation_id=correlation_id,
            _request_id=request_id,
            _timeout_hint=timeout_hint,  # Pass timeout hint from upstream
            **kwargs,
        ):
            # Type guard: ensure chunk is a dict before accessing .get()
            if not isinstance(chunk, dict):
                logger.warning(
                    f"Unexpected chunk type: {type(chunk).__name__}, skipping"
                )
                continue

            # Capture stream_id from first chunk
            if chunk.get("_type") == "stream_id":
                stream_id = chunk.get("stream_id")
                logger.info(f"🔗 [REQ:{request_id[:8]}] Stream ID: {stream_id}")
                continue

            chunk_count += 1
            if chunk_count == 1:
                logger.info(
                    f"📤 [REQ:{request_id[:8]}] First chunk for {model_id} "
                    f"(stream_id={stream_id or 'unknown'})"
                )

            # Convert to OpenAI format if needed
            if "choices" in chunk and chunk["choices"]:
                chunk = convert_worker_chunk_to_openai_format(chunk)

            event = create_ndjson_event("chunk", chunk)
            yield serialize_ndjson_event(event)

        if chunk_count == 0:
            logger.error(
                f"❌ [REQ:{request_id[:8]}] Stream completed with ZERO chunks "
                f"for {model_id} (stream_id={stream_id or 'unknown'})"
            )
        else:
            logger.info(
                f"✅ [REQ:{request_id[:8]}] Stream completed "
                f"(chunks={chunk_count}, stream_id={stream_id or 'unknown'})"
            )

    except asyncio.CancelledError:
        logger.info(f"🔌 Client disconnected for {model_id}")
        raise

    except TimeoutError as e:
        for item in iter_timeout_error_events(str(e), request_id):
            yield item

    except StreamError as e:
        logger.error(f"StreamError for {model_id}: {e.code}: {e.message}")
        for item in iter_error_and_complete_events(e.message, "server_error", e.code):
            yield item

    except RuntimeError as e:
        for item in iter_runtime_error_events(e, model_id, request_id):
            yield item

    except ProcessError as e:
        for item in iter_process_error_events(e, model_id, request_id):
            yield item

    except Exception as e:
        logger.error(f"❌ Unhandled exception for {model_id}: {e}", exc_info=True)
        for item in iter_error_and_complete_events(
            f"Unexpected error: {type(e).__name__}: {e}",
            "server_error",
            ErrorCode.UNEXPECTED_ERROR,
        ):
            yield item
