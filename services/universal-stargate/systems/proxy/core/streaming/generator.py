"""
Stream generator for SSE response handling.

Contains the async generator that processes NDJSON from gateway and converts to SSE.
"""

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from src.core.streaming.ndjson_framing import iter_ndjson_lines_bytes

if TYPE_CHECKING:
    from ...utils.request_context import ForwardContext
    from ..common import ChunkProcessor
    from .monitor import StreamMonitor

logger = get_logger(__name__)


def has_finish_reason(payload: dict[str, Any]) -> bool:
    """Check if payload has finish_reason indicating stream completion."""
    if not payload or not isinstance(payload, dict):
        return False
    if payload.get("finish_reason"):
        return True
    if "choices" in payload:
        for choice in payload["choices"]:
            if choice.get("finish_reason"):
                return True
    return False


async def create_stream_generator(
    http_client,
    method: str,
    url: str,
    headers: dict[str, str],
    content: bytes | None,
    streaming_timeout: float,
    context: "ForwardContext",
    chunk_processor: "ChunkProcessor",
    stream_monitor: "StreamMonitor",
    gateway_name: str,
    request=None,
):
    """
    Create an async generator that streams NDJSON from gateway and converts to SSE.

    Args:
        http_client: HTTP client for gateway requests
        method: HTTP method
        url: Gateway URL
        headers: Request headers
        content: Request body
        streaming_timeout: Timeout for streaming
        context: Forward context with request metadata
        chunk_processor: Processor for NDJSON chunks
        stream_monitor: Monitor for logging chunks
        gateway_name: Name of the gateway
        request: FastAPI Request for disconnection detection

    Yields:
        SSE formatted chunks
    """
    request_short_id = context.request_id[:8] if context else "unknown"
    logger.info(f"🎬 [REQ:{request_short_id}] STREAM GENERATOR STARTED")

    client_disconnected = False
    stream_completed = False
    response_closed = False

    try:
        if context:
            asyncio.create_task(stream_monitor.send_initial_completion_event(context))

        logger.info(
            f"🌐 INITIATING HTTP STREAM to {url} for request {context.request_id}"
        )

        async with http_client.stream(
            method=method,
            url=url,
            headers=headers,
            content=content,
            timeout=streaming_timeout,
        ) as response:
            if response.status_code != 200:
                async for event in _handle_error_response(
                    response, context, client_disconnected
                ):
                    yield event
                return

            chunk_count = 0
            total_time = 0.0

            logger.info(
                f"📥 [REQ:{request_short_id}] Starting to read stream from Gateway "
                f"(gateway={gateway_name})"
            )

            try:
                async for line_bytes in iter_ndjson_lines_bytes(response):
                    if stream_completed or client_disconnected:
                        reason = (
                            "stream completed"
                            if stream_completed
                            else "client disconnected"
                        )
                        logger.info(f"🔌 Breaking stream loop - {reason}")
                        break

                    chunk_start = time.perf_counter()
                    processed = chunk_processor.process_chunk(line_bytes, context)

                    if chunk_count == 0:
                        logger.info(
                            f"📨 [REQ:{request_short_id}] First line received "
                            f"(length={len(line_bytes)})"
                        )

                    if processed is None:
                        # Decode only for the warning log path (not hot path)
                        logger.warning(
                            "⚠️ [REQ:%s] Non-JSON line, skipping: %s",
                            request_short_id,
                            repr(line_bytes[:100]),
                        )
                        continue

                    # Increment chunk count now that we have a valid chunk
                    chunk_count += 1

                    # Process chunk and yield
                    result = await _process_and_yield_chunk(
                        processed,
                        chunk_count,
                        context,
                        stream_monitor,
                        chunk_processor,
                        response,
                        response_closed,
                        client_disconnected,
                        stream_completed,
                        request_short_id,
                    )

                    if result.get("break"):
                        client_disconnected = result.get("disconnected", False)
                        stream_completed = result.get("completed", False)
                        response_closed = result.get("response_closed", response_closed)
                        if result.get("yield"):
                            yield result["yield"]
                        break

                    if result.get("yield"):
                        yield result["yield"]

                    if result.get("completed"):
                        stream_completed = True
                        break

                    total_time += (time.perf_counter() - chunk_start) * 1000

            except (GeneratorExit, asyncio.CancelledError):
                logger.info(
                    f"🔌 CLIENT DISCONNECTED: stopping stream for request "
                    f"{context.request_id if context else 'unknown'}"
                )

    except (GeneratorExit, asyncio.CancelledError):
        req_id = context.request_id if context else "unknown"
        logger.info(f"🔌 STREAM CANCELLED for request {req_id}")

    except Exception as e:
        if not stream_completed:
            logger.error(f"Streaming error: {e} (type: {type(e).__name__})")
            error_content = json.dumps(
                {"error": {"message": str(e), "type": "streaming_error"}}
            ).encode("utf-8")
            try:
                yield error_content
            except (GeneratorExit, asyncio.CancelledError, ConnectionError):
                pass

    finally:
        if (
            "response" in locals()
            and hasattr(response, "aclose")
            and not response_closed
        ):
            try:
                await response.aclose()
                logger.info("🔌 Closed backend HTTP stream in finally block")
            except Exception:
                pass

        chunk_count_final = chunk_count if "chunk_count" in locals() else 0
        total_time_final = total_time if "total_time" in locals() else 0.0
        stream_monitor.log_stream_completion(
            context, chunk_count_final, total_time_final
        )

        logger.info(
            f"🏁 STREAM GENERATOR COMPLETED for request {context.request_id} - "
            f"chunks: {chunk_count_final}, disconnected: {client_disconnected}"
        )


async def _handle_error_response(response, context, client_disconnected):
    """Handle non-200 response from gateway."""
    error_message = f"Gateway returned status {response.status_code}"
    gateway_error_details = {}

    try:
        error_data = await response.aread()
        if error_data:
            error_json = json.loads(error_data.decode("utf-8"))
            error_message = error_json.get("error", {}).get("message", error_message)
            gateway_error_details = {
                "gateway_error": error_json.get("error", {}),
                "gateway_status": response.status_code,
            }
    except Exception as parse_error:
        gateway_error_details = {
            "gateway_error": {"message": error_message, "type": "unknown"},
            "gateway_status": response.status_code,
            "parse_error": str(parse_error),
        }

    logger.error(f"Gateway streaming error: {response.status_code} - {error_message}")

    error_content = json.dumps(
        {
            "error": {
                "message": f"Gateway streaming error: {error_message}",
                "type": "gateway_error",
                "status_code": response.status_code,
                "gateway_details": gateway_error_details,
            }
        }
    ).encode("utf-8")

    try:
        yield error_content
    except (GeneratorExit, asyncio.CancelledError, ConnectionError):
        pass


async def _process_and_yield_chunk(
    processed,
    chunk_count,
    context,
    stream_monitor,
    chunk_processor,
    response,
    response_closed,
    client_disconnected,
    stream_completed,
    request_short_id,
) -> dict:
    """Process a chunk and determine what to yield."""
    result: dict[str, Any] = {}

    # Handle [DONE] marker
    if processed.is_done:
        if context:
            asyncio.create_task(
                stream_monitor.monitor_chunk_async(
                    chunk_content="[DONE]",
                    chunk_number=chunk_count,
                    request_id=context.request_id,
                    token_metrics=context.token_metrics,
                )
            )
        try:
            result["yield"] = processed.sse_format
        except (GeneratorExit, asyncio.CancelledError, ConnectionError):
            result["break"] = True
            result["disconnected"] = True
        return result

    # Monitor chunk
    should_monitor = False
    monitor_content = ""
    if processed.chunk_content:
        should_monitor = True
        monitor_content = processed.chunk_content
    elif processed.payload and has_finish_reason(processed.payload):
        should_monitor = True
        finish_reason = None
        if isinstance(processed.payload, dict):
            finish_reason = processed.payload.get("finish_reason")
            if (
                not finish_reason
                and "choices" in processed.payload
                and processed.payload["choices"]
            ):
                finish_reason = processed.payload["choices"][0].get("finish_reason")
        monitor_content = (
            f"[finish_reason: {finish_reason}]"
            if finish_reason
            else "[finish_reason: stop]"
        )

    if should_monitor and context:
        asyncio.create_task(
            stream_monitor.monitor_chunk_async(
                chunk_content=monitor_content,
                chunk_number=chunk_count,
                request_id=context.request_id,
                token_metrics=context.token_metrics,
            )
        )

    # Forward to client
    if processed.should_yield:
        try:
            result["yield"] = processed.sse_format
            if has_finish_reason(processed.payload):
                result["completed"] = True
        except (GeneratorExit, asyncio.CancelledError, ConnectionError):
            result["break"] = True
            result["disconnected"] = True

        if processed.trimmed_content is not None:
            chunk_processor.clear_filter_pending()

    return result
