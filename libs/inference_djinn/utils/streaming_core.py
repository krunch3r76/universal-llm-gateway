"""
Unified streaming core utilities for inference_djinn.

Provides centralized streaming logic with cancellation support,
OpenAI format conversion, and consistent error handling.
"""

import asyncio
from universal_logging import get_logger
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

logger = get_logger(__name__)


async def iterate_blocking(gen: Iterator[Any]) -> AsyncIterator[Any]:
    """
    Convert blocking generator to async iterator using hybrid approach.

    Uses thread pool ONLY for first token (long TTFT during prefill), then
    switches to direct iteration for subsequent tokens (fast, minimal overhead).

    This provides:
    - Event loop responsiveness during long prefill (ping/pong works)
    - Minimal overhead during token generation (no thread scheduling per token)

    Args:
        gen: Blocking generator/iterator (e.g., llama-cpp-python streaming)

    Yields:
        Items from the blocking generator
    """
    loop = asyncio.get_running_loop()

    def _next_or_stop():
        """Get next item or raise StopIteration."""
        return next(gen)

    try:
        # First token: use thread pool (TTFT can be 30-60+ seconds)
        # This keeps event loop responsive for WebSocket ping/pong
        try:
            first_item = await loop.run_in_executor(None, _next_or_stop)
            yield first_item
        except StopIteration:
            return

        # Subsequent tokens: direct iteration with cooperative yielding
        # Token generation is fast (milliseconds), no need for thread overhead
        for item in gen:
            yield item
            # Cooperative yield every token to allow other async tasks
            await asyncio.sleep(0)

    except Exception as e:
        logger.error(f"Error in blocking generator iteration: {e}")
        raise


def extract_content_from_chunk(chunk: Any, is_chat: bool = False) -> str | None:
    """
    Extract content from various chunk formats.

    Args:
        chunk: Chunk from engine (str, dict, or other format)
        is_chat: Whether this is a chat completion format

    Returns:
        Extracted content string or None
    """
    if isinstance(chunk, str):
        return chunk  # Don't strip - preserve whitespace

    if isinstance(chunk, dict):
        # OpenAI format with choices
        if "choices" in chunk and chunk["choices"]:
            choice = chunk["choices"][0]
            if is_chat:
                # Chat format: delta.content
                delta = choice.get("delta", {})
                content = delta.get("content", "")
                # Handle None content
                if content is None:
                    return ""
                return content
            else:
                # Completion format: text
                return choice.get("text", "")

        # Direct content field
        return chunk.get("content", "")

    return None


def create_openai_chunk(
    content: str,
    model_name: str,
    chunk_id: str | None = None,
    finish_reason: str | None = None,
) -> dict[str, Any]:
    """
    Create OpenAI-compliant streaming chunk.

    Args:
        content: Text content to include
        model_name: Model name for the chunk
        chunk_id: Optional chunk ID (generated if not provided)
        finish_reason: Optional finish reason

    Returns:
        OpenAI-compliant chunk dictionary
    """
    if chunk_id is None:
        chunk_id = f"chatcmpl-{int(time.time() * 1000)}"

    return {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model_name,
        "choices": [
            {
                "index": 0,
                "delta": {"content": content} if content else {},
                "finish_reason": finish_reason,
            }
        ],
    }


async def emit_openai_stream(
    chunks: AsyncIterator[Any],
    model_name: str,
    is_chat: bool = False,
    cancellation_event: asyncio.Event | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """
    Convert engine chunks to OpenAI streaming format.

    This is the unified streaming core that handles:
    - Cancellation checks
    - Content extraction
    - OpenAI format conversion
    - Final chunk emission
    - Error handling (exceptions only, no error chunks)

    Args:
        chunks: Async iterator of engine chunks
        model_name: Model name for OpenAI chunks
        is_chat: Whether this is chat completion format
        cancellation_event: Optional event to signal cancellation of streaming.
            When set, the streaming will stop gracefully after the current chunk.
            Supports multiple cancellation sources:
            - Client disconnection (FastAPI raises asyncio.CancelledError)
            - Explicit cancellation via management API
            - Timeout enforcement
            - Resource limits (GPU memory, system constraints)

    Yields:
        OpenAI-compliant streaming chunks with finish_reason="cancelled" on cancellation

    Raises:
        RuntimeError: If streaming fails (never yields error chunks)
    """
    finish_reason = "stop"
    chunk_count = 0
    start_time = time.time()

    try:
        async for chunk in chunks:
            chunk_count += 1

            # DEBUG: Log raw chunk
            logger.debug(
                f"🔍 [StreamingCore] Raw chunk #{chunk_count}: {type(chunk)} - {repr(chunk)[:200]}"
            )

            # Check for cancellation before processing
            if cancellation_event and cancellation_event.is_set():
                finish_reason = "cancelled"
                logger.info(f"Streaming cancelled after {chunk_count} chunks")
                break

            # Extract content from chunk
            content = extract_content_from_chunk(chunk, is_chat)

            # DEBUG: Log extracted content
            logger.debug(
                f"🔍 [StreamingCore] Extracted content: {repr(content)[:100] if content is not None else 'None'}"
            )

            if content is not None:
                # Only yield if we have actual content (not empty string)
                if content:
                    logger.debug(
                        f"🔍 [StreamingCore] Yielding chunk with content: {repr(content[:50])}"
                    )
                    yield create_openai_chunk(content, model_name)
                else:
                    logger.debug("🔍 [StreamingCore] Skipping chunk with empty content")
            else:
                logger.debug(f"🔍 [StreamingCore] Empty content chunk: {chunk}")

            # Check for finish reason in chunk
            if isinstance(chunk, dict) and "choices" in chunk and chunk["choices"]:
                choice = chunk["choices"][0]
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]
                    break

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Streaming error: {type(e).__name__}: {error_msg}")
        # Always raise exceptions - never yield error chunks
        raise RuntimeError(f"Streaming generation failed: {error_msg}") from e

    finally:
        elapsed = time.time() - start_time
        logger.debug(f"Stream complete: {chunk_count} chunks, {elapsed:.2f}s total")

        # Always emit final chunk
        yield create_openai_chunk("", model_name, finish_reason=finish_reason)
