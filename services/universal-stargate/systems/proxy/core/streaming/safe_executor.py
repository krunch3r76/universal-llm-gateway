"""
Streaming Safe Executor Module

Provides wrapper for executing streaming requests with proper error handling.

Tracks streaming state and ensures errors are handled appropriately:
- Errors before first chunk → HTTP error response
- Errors after first chunk → SSE error event
- Empty streams → Query gateway for crash details
"""

from collections.abc import AsyncGenerator, Callable

from fastapi import HTTPException
from universal_logging import get_logger

from ..common import ErrorNormalizer
from .error_handler import StreamingErrorHandler
from .state_tracker import StreamStateTracker

logger = get_logger(__name__)


class StreamingSafeExecutor:
    """
    Execute streaming requests with automatic error handling.

    Tracks streaming state and ensures errors are handled appropriately:
    - Errors before first chunk → HTTP error response
    - Errors after first chunk → SSE error event
    """

    def __init__(self, error_normalizer: type[ErrorNormalizer]):
        """
        Initialize streaming safe executor.

        Args:
            error_normalizer: ErrorNormalizer class for error formatting
        """
        self.error_normalizer = error_normalizer

    async def _query_model_status(self, gateway_url: str, model_id: str) -> dict | None:
        """
        Query gateway for model status after empty stream.

        This helper method calls the Gateway's /api/v1/status/detailed endpoint
        to retrieve the current status of a model.

        Args:
            gateway_url: Base URL of the gateway
            model_id: Model ID to query status for

        Returns:
            Model status dict or None if query fails
        """
        try:
            import httpx

            limits = httpx.Limits(
                max_keepalive_connections=2,
                max_connections=5,
                keepalive_expiry=10.0,
            )

            async with httpx.AsyncClient(
                timeout=5.0,
                limits=limits,
                http2=False,
            ) as client:
                response = await client.get(f"{gateway_url}/api/v1/status/detailed")

                if response.status_code == 200:
                    data = response.json()
                    models = data.get("models", {})

                    if model_id in models:
                        return models[model_id]
                    logger.debug(f"Model {model_id} not found in gateway status")
                    return None
                logger.warning(
                    f"Gateway status query failed: HTTP {response.status_code}"
                )
                return None

        except Exception as e:
            logger.warning(f"Failed to query model status from gateway: {e}")
            return None

    async def execute_streaming_request(
        self,
        stream_func: Callable[[], AsyncGenerator],
        request_id: str,
        operation: str = "streaming_request",
        gateway_url: str | None = None,
        model_id: str | None = None,
    ) -> AsyncGenerator:
        """
        Execute streaming function with error handling.

        Args:
            stream_func: Async function that returns async generator
            request_id: Request ID for logging/tracking
            operation: Operation name for error context
            gateway_url: Optional gateway URL for crash detection
            model_id: Optional model ID for crash detection

        Yields:
            SSE chunks from stream_func, or error events on exception

        Raises:
            HTTPException: If error occurs before first chunk
        """
        stream_started = False
        chunk_count = 0

        try:
            logger.info(
                f"🔧 [{request_id[:8]}] Calling stream_func() to start generator"
            )
            generator = stream_func()
            logger.info(
                f"🔄 [{request_id[:8]}] About to start async for loop on generator"
            )

            async for chunk in generator:
                stream_started = True
                chunk_count += 1
                yield chunk

            logger.info(
                f"🔚 [{request_id[:8]}] Async for loop completed, chunk_count={chunk_count}"
            )

            # Empty stream detection
            if chunk_count == 0:
                async for event in self._handle_empty_stream(
                    request_id, gateway_url, model_id
                ):
                    yield event

        except HTTPException as exc:
            logger.info(
                f"🚨 [{request_id[:8]}] Caught HTTPException: {exc.status_code} - {exc.detail}"
            )
            if stream_started:
                async for event in self._handle_streaming_http_exception(
                    exc, request_id, operation
                ):
                    yield event
            else:
                logger.error(
                    f"[{request_id}] HTTPException before streaming started ({operation}): "
                    f"{exc.detail}",
                    exc_info=True,
                )
                raise

        except Exception as exc:
            logger.info(
                f"🚨 [{request_id[:8]}] Caught Exception: {type(exc).__name__} - {str(exc)}"
            )
            status, error_dict = self.error_normalizer.normalize_to_openai_format(
                error=exc, default_status=500, operation=operation
            )

            if stream_started:
                logger.error(
                    f"[{request_id}] Exception during streaming ({operation}): {exc}",
                    exc_info=True,
                )
                yield StreamingErrorHandler.create_sse_error_event(error_dict)
                yield StreamingErrorHandler.create_sse_done_event()
            else:
                logger.error(
                    f"[{request_id}] Exception before streaming started ({operation}): {exc}",
                    exc_info=True,
                )
                raise HTTPException(status_code=status, detail=error_dict)

    async def _handle_empty_stream(
        self,
        request_id: str,
        gateway_url: str | None,
        model_id: str | None,
    ) -> AsyncGenerator:
        """Handle empty stream (0 chunks) - likely worker crash."""
        logger.warning(
            f"[{request_id}] Empty stream detected (0 chunks) - likely worker crash"
        )

        crash_reason = None
        if gateway_url and model_id:
            logger.info(f"[{request_id}] Querying gateway for model status: {model_id}")
            model_status = await self._query_model_status(gateway_url, model_id)

            if model_status and model_status.get("status") == "error":
                crash_reason = model_status.get("error_message")
                logger.error(
                    f"[{request_id}] Confirmed worker crash for {model_id}: {crash_reason}"
                )

        if crash_reason:
            error_detail = {
                "error": {
                    "message": f"Worker crashed before generating response: {crash_reason}",
                    "type": "service_unavailable",
                    "code": "worker_crashed",
                    "request_id": request_id,
                }
            }
        else:
            error_detail = {
                "error": {
                    "message": "Stream completed without data - worker may havecrashed",
                    "type": "service_unavailable",
                    "code": "empty_stream",
                    "request_id": request_id,
                }
            }

        logger.info(f"[{request_id}] Sending empty stream error as SSE event")
        yield StreamingErrorHandler.create_sse_error_event(error_detail)
        yield StreamingErrorHandler.create_sse_done_event()

    async def _handle_streaming_http_exception(
        self,
        exc: HTTPException,
        request_id: str,
        operation: str,
    ) -> AsyncGenerator:
        """Handle HTTPException during active streaming."""
        logger.error(
            f"[{request_id}] HTTPException during streaming ({operation}): {exc.detail}",
            exc_info=True,
        )

        status = exc.status_code
        detail = exc.detail

        if isinstance(detail, dict) and "error" in detail:
            error_dict = detail
        else:
            status, error_dict = self.error_normalizer.normalize_to_openai_format(
                error=exc, default_status=status, operation=operation
            )

        yield StreamingErrorHandler.create_sse_error_event(error_dict)
        yield StreamingErrorHandler.create_sse_done_event()

    async def execute_with_state_tracker(
        self,
        stream_func: Callable[[StreamStateTracker], AsyncGenerator],
        request_id: str,
        operation: str = "streaming_request",
        gateway_url: str | None = None,
        model_id: str | None = None,
    ) -> AsyncGenerator:
        """
        Execute streaming function with explicit state tracker.

        Args:
            stream_func: Async function that takes StreamStateTracker
            request_id: Request ID for logging/tracking
            operation: Operation name for error context
            gateway_url: Optional gateway URL for crash detection
            model_id: Optional model ID for crash detection

        Yields:
            SSE chunks from stream_func, or error events on exception
        """
        state_tracker = StreamStateTracker()
        chunk_count = 0

        try:
            async for chunk in stream_func(state_tracker):
                if not state_tracker.stream_started:
                    state_tracker.mark_first_chunk_sent()
                chunk_count += 1
                yield chunk

            if chunk_count == 0:
                async for event in self._handle_empty_stream(
                    request_id, gateway_url, model_id
                ):
                    yield event

        except HTTPException as exc:
            if state_tracker.stream_started:
                async for event in self._handle_streaming_http_exception(
                    exc, request_id, operation
                ):
                    yield event
            else:
                logger.error(
                    f"[{request_id}] HTTPException before streaming started ({operation}): "
                    f"{exc.detail}",
                    exc_info=True,
                )
                raise

        except Exception as exc:
            status, error_dict = self.error_normalizer.normalize_to_openai_format(
                error=exc, default_status=500, operation=operation
            )

            if state_tracker.stream_started:
                logger.error(
                    f"[{request_id}] Exception during streaming ({operation}): {exc}",
                    exc_info=True,
                )
                yield StreamingErrorHandler.create_sse_error_event(error_dict)
                yield StreamingErrorHandler.create_sse_done_event()
            else:
                logger.error(
                    f"[{request_id}] Exception before streaming started ({operation}): {exc}",
                    exc_info=True,
                )
                raise HTTPException(status_code=status, detail=error_dict)
