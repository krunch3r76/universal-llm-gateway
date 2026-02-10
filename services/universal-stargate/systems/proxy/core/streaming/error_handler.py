"""
Streaming Error Handling Module

Provides streaming-aware error handling for SSE (Server-Sent Events) requests.

Handles errors differently based on streaming state:
- Before streaming starts (no HTTP headers sent): Return HTTP error response
- After streaming starts (HTTP 200 sent): Send SSE error event + [DONE]

Usage:
    # Method 1: Handle error with explicit state tracking
    error_dict = ErrorNormalizer.normalize_to_openai_format(exc)
    if stream_started:
        sse_event = StreamingErrorHandler.create_sse_error_event(error_dict)
        yield sse_event
        yield StreamingErrorHandler.create_sse_done_event()
    else:
        raise HTTPException(status_code=status, detail=error_dict)

    # Method 2: Wrap generator with automatic error handling
    safe_gen = StreamingErrorHandler.wrap_streaming_generator(
        original_gen,
        request_id="req-123",
        error_normalizer=ErrorNormalizer
    )
    async for chunk in safe_gen:
        yield chunk

Architecture:
    Client → StreamingSafeExecutor → StreamingFunction
                    ↓ (error)
              ErrorNormalizer → OpenAI format
                    ↓
         StreamingErrorHandler → SSE event or HTTPException
"""

import json
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import HTTPException
from universal_logging import get_logger

from ..common import ErrorNormalizer

logger = get_logger(__name__)


class StreamingErrorHandler:
    """
    Handle errors for streaming requests with awareness of stream state.

    Pre-stream: Return HTTP error (via HTTPException)
    In-stream: Send SSE error event, then close
    """

    @staticmethod
    async def handle_streaming_error(
        error: Exception,
        stream_started: bool,
        request_id: str,
        error_normalizer: type[ErrorNormalizer],
        operation: str = "streaming",
    ) -> str:
        """
        Handle error based on streaming state.

        Args:
            error: The exception that occurred
            stream_started: Whether streaming has begun (HTTP headers sent)
            request_id: Request ID for logging
            error_normalizer: ErrorNormalizer class from Phase 1
            operation: Operation context for error message

        Returns:
            str: SSE error event string (when stream_started=True)

        Raises:
            HTTPException: When stream hasn't started (stream_started=False)

        Examples:
            # Pre-stream error (raise HTTPException)
            try:
                result = await handle_streaming_error(
                    error=ValueError("Bad input"),
                    stream_started=False,
                    request_id="req-123",
                    error_normalizer=ErrorNormalizer
                )
            except HTTPException as exc:
                # Handle HTTP error
                pass

            # In-stream error (return SSE event)
            sse_event = await handle_streaming_error(
                error=ValueError("Stream failed"),
                stream_started=True,
                request_id="req-123",
                error_normalizer=ErrorNormalizer
            )
            # sse_event = "data: {\"error\": {...}}\\n\\n"
        """
        # Normalize error using Phase 1's ErrorNormalizer
        if isinstance(error, HTTPException):
            # HTTPException already has status and potentially OpenAI format detail
            status = error.status_code
            detail = error.detail

            # If detail is already OpenAI format, extract it
            if isinstance(detail, dict) and "error" in detail:
                error_dict = detail
            else:
                # Re-normalize through ErrorNormalizer
                status, error_dict = error_normalizer.normalize_to_openai_format(
                    error=error, default_status=status, operation=operation
                )
        else:
            # Other exceptions need normalization
            status, error_dict = error_normalizer.normalize_to_openai_format(
                error=error, default_status=500, operation=operation
            )

        # Handle based on streaming state
        if stream_started:
            # Stream active: Send SSE error event
            logger.error(
                f"[{request_id}] Error during active streaming: {error}", exc_info=True
            )
            return StreamingErrorHandler.create_sse_error_event(error_dict)
        else:
            # Stream not started: Raise HTTP error
            logger.error(
                f"[{request_id}] Error before streaming started: {error}", exc_info=True
            )
            raise HTTPException(status_code=status, detail=error_dict)

    @staticmethod
    def create_sse_error_event(error_dict: dict[str, Any]) -> str:
        """
        Format error as SSE event.

        Args:
            error_dict: OpenAI format error dict (from ErrorNormalizer)

        Returns:
            SSE formatted event: "data: {...}\\n\\n"

        Examples:
            >>> error_dict = {
            ...     "error": {
            ...         "message": "Model failed to load",
            ...         "type": "model_error",
            ...         "code": "model_loading_failed"
            ...     }
            ... }
            >>> sse_event = StreamingErrorHandler.create_sse_error_event(error_dict)
            >>> print(sse_event)
            data: {"error": {"message": "Model failed to load", ...}}

        """
        try:
            # Serialize to JSON (single-line, no newlines in JSON)
            json_str = json.dumps(error_dict, separators=(",", ":"), ensure_ascii=False)

            # Format as SSE event
            # SSE format: "data: {json}\\n\\n" (double newline = event terminator)
            return f"data: {json_str}\n\n"

        except Exception as e:
            # Fallback if JSON serialization fails
            logger.error(f"Failed to serialize error to SSE format: {e}", exc_info=True)
            fallback_error = {
                "error": {
                    "message": "An error occurred and could not be properly formatted",
                    "type": "api_error",
                    "code": "serialization_failed",
                }
            }
            json_str = json.dumps(fallback_error, separators=(",", ":"))
            return f"data: {json_str}\n\n"

    @staticmethod
    def create_sse_done_event() -> str:
        """
        Return SSE [DONE] marker to signal end of stream.

        Returns:
            SSE formatted DONE event: "data: [DONE]\\n\\n"

        Examples:
            >>> done_event = StreamingErrorHandler.create_sse_done_event()
            >>> print(done_event)
            data: [DONE]

        """
        return "data: [DONE]\n\n"

    @staticmethod
    async def wrap_streaming_generator(
        generator: AsyncGenerator,
        request_id: str,
        error_normalizer: type[ErrorNormalizer],
        operation: str = "streaming",
    ) -> AsyncGenerator:
        """
        Wrap an async generator with error handling.

        Catches exceptions during iteration and converts them to
        SSE error events, then closes the stream gracefully.

        Args:
            generator: Original async generator to wrap
            request_id: Request ID for logging/tracking
            error_normalizer: ErrorNormalizer class from Phase 1
            operation: Operation name for error context

        Yields:
            Chunks from original generator, or error events on exception

        Raises:
            HTTPException: If error occurs before first chunk (pre-stream)

        Examples:
            # Wrap a streaming function
            original_gen = some_streaming_function()
            safe_gen = StreamingErrorHandler.wrap_streaming_generator(
                original_gen,
                request_id="req-123",
                error_normalizer=ErrorNormalizer,
                operation="inference"
            )

            # Use the wrapped generator
            async for chunk in safe_gen:
                yield chunk  # Will automatically handle errors
        """
        stream_started = False

        try:
            async for chunk in generator:
                stream_started = True
                yield chunk

        except HTTPException as exc:
            # HTTPException already in correct format
            if stream_started:
                # Stream active: Convert to SSE error event
                logger.error(
                    f"[{request_id}] HTTPException during streaming: {exc.detail}",
                    exc_info=True,
                )

                status = exc.status_code
                detail = exc.detail

                # If detail is already OpenAI format, use it directly
                if isinstance(detail, dict) and "error" in detail:
                    error_dict = detail
                else:
                    # Re-normalize
                    status, error_dict = error_normalizer.normalize_to_openai_format(
                        error=exc, default_status=status, operation=operation
                    )

                yield StreamingErrorHandler.create_sse_error_event(error_dict)
                yield StreamingErrorHandler.create_sse_done_event()
            else:
                # Stream not started: Re-raise as HTTP error
                raise

        except Exception as exc:
            # Other exceptions need normalization
            if stream_started:
                # Stream active: Convert to SSE error event
                logger.error(
                    f"[{request_id}] Exception during streaming: {exc}", exc_info=True
                )

                status, error_dict = error_normalizer.normalize_to_openai_format(
                    error=exc, default_status=500, operation=operation
                )

                yield StreamingErrorHandler.create_sse_error_event(error_dict)
                yield StreamingErrorHandler.create_sse_done_event()
            else:
                # Stream not started: Normalize and raise as HTTP error
                status, error_dict = error_normalizer.normalize_to_openai_format(
                    error=exc, default_status=500, operation=operation
                )
                raise HTTPException(status_code=status, detail=error_dict)
