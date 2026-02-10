"""
Streaming response wrappers for concurrency and tracking.

Provides utilities for wrapping streaming responses with tracking completion
and lifecycle event emission.
"""

import asyncio
import time
from typing import TYPE_CHECKING

from fastapi.responses import StreamingResponse
from universal_logging import get_logger

from src.scheduling.events import (
    RequestCompleted,
    RequestFailed,
)

if TYPE_CHECKING:
    from universal_event_bus import EventBus

    from ..nonstreaming.preparer import RequestContext

logger = get_logger(__name__)


def wrap_streaming_response_for_tracking(
    response: StreamingResponse,
    context: "RequestContext",
    model_id: str,
    start_time: float,
    event_bus: "EventBus",
    gateway_id: str,
) -> StreamingResponse:
    """
    Wrap streaming response for tracking and event emission.

    Events are emitted for observability only. Capacity is released deterministically
    in the forward path's `finally` blocks.
    """
    if context.selected_gateway:
        gateway_url = getattr(context.selected_gateway.ref, "remote_stargate_url", None)
    elif context.selected_gateway_instance:
        gateway_url = context.selected_gateway_instance.config.base_url
    else:
        gateway_url = None

    async def release_on_close():
        """Emit completion events when streaming terminates."""
        stream_error: Exception | None = None
        stream_succeeded = False
        try:
            async for chunk in response.body_iterator:
                yield chunk
            stream_succeeded = True
        except (GeneratorExit, asyncio.CancelledError):
            # Client disconnected - log and ensure slot release
            logger.warning(
                "🔌 [REQ:%s] Client disconnect during streaming for %s",
                context.request_id[:8],
                model_id,
            )
            raise
        except Exception as e:
            # Error path - capture exception for event
            stream_error = e
            logger.error(
                "❌ [REQ:%s] Stream error for %s: %s",
                context.request_id[:8],
                model_id,
                e,
            )
            raise
        finally:
            # CRITICAL: Always emit completion events, even on disconnect
            from systems.proxy.core.lifecycle import emit_execution_completed

            # Emit lifecycle event first (success/failure)
            try:
                if stream_succeeded:
                    await event_bus.publish_async_nowait(
                        RequestCompleted(
                            request_id=context.request_id,
                            gateway_url=gateway_url,
                            model_id=model_id,
                            duration=time.time() - start_time,
                        )
                    )
                elif stream_error is not None:
                    await event_bus.publish_async_nowait(
                        RequestFailed(
                            request_id=context.request_id,
                            model_id=model_id,
                            error=str(stream_error),
                            gateway_url=gateway_url,
                        )
                    )
            except Exception as e:
                logger.error("Failed to emit lifecycle event: %s", e)

            # Always emit execution completed for lifecycle tracking
            try:
                await emit_execution_completed(
                    event_bus,
                    url=gateway_url or "unknown",
                    model_id=model_id,
                    request_id=context.request_id,
                    gateway_id=gateway_id,
                )
            except Exception as e:
                logger.error("Failed to emit execution completed: %s", e)

    return StreamingResponse(
        release_on_close(),
        status_code=response.status_code,
        headers=response.headers,
        media_type=response.media_type,
        background=response.background,
    )
