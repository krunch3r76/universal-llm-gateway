"""Streaming inference loop (worker-side)."""

import asyncio
from collections.abc import Callable
from typing import Any

from universal_logging import get_logger
from universal_protocol.errors import EngineError

from ..deadline import enforce_idle_timeout

logger = get_logger(__name__)


class StreamInferenceRunHandlers:
    """Mix-in class providing the streaming inference loop.

    Assumes: self.engine, self._inference_gate, self._map_exception_to_engine_error.
    """

    async def stream_inference(
        self,
        stream_id: str,
        context: Any,
        queue: Any,
        cancellation_event: asyncio.Event,
        prompt: str | None,
        messages: list[dict[str, str]] | None,
        parameters: dict[str, Any],
        request_id: str = "unknown",
    ) -> None:
        """Stream inference results to WebSocket.

        Acquires inference slot from gate before calling engine.
        """
        timeout_hint = parameters.get("timeout_hint")

        gate = self._inference_gate
        if not gate:
            raise EngineError(
                code="INTERNAL_ERROR",
                message="Inference gate not initialized",
            )

        try:
            async with enforce_idle_timeout(
                timeout_hint, cancellation_event, request_id
            ) as reset_idle:
                try:
                    await gate.acquire(
                        request_id,
                        timeout=timeout_hint,
                        cancellation_event=cancellation_event,
                    )
                    try:
                        await self._run_streaming_inference(
                            stream_id=stream_id,
                            queue=queue,
                            cancellation_event=cancellation_event,
                            prompt=prompt,
                            messages=messages,
                            parameters=parameters,
                            request_id=request_id,
                            reset_idle=reset_idle,
                        )
                    finally:
                        await asyncio.shield(gate.release())
                except TimeoutError:
                    logger.error(
                        f"❌ [worker] [{request_id}] Queue timeout for stream "
                        f"{stream_id}"
                    )
                    error_frame = {
                        "t": "err",
                        "code": "QUEUE_TIMEOUT",
                        "message": (
                            f"Timeout waiting for inference slot ({timeout_hint}s)"
                        ),
                        "source": "worker",
                        "data": {},
                    }
                    try:
                        await queue.put(error_frame)
                    except Exception:
                        pass
                    return
                except asyncio.CancelledError:
                    logger.info(
                        f"🛑 [worker] [{request_id}] Cancelled while queued "
                        f"for {stream_id}"
                    )
                    error_frame = {
                        "t": "err",
                        "code": "CANCELLED",
                        "message": "Request cancelled while waiting for slot",
                        "source": "worker",
                        "data": {},
                    }
                    try:
                        await queue.put(error_frame)
                    except Exception:
                        pass
                    raise

        finally:
            # Signal cleanup complete after engine teardown
            from universal_protocol.ws.registry import stream_registry

            entry = stream_registry.get(stream_id)
            if entry:
                entry.mark_cleanup_complete()
                logger.debug(
                    f"[worker] [request_id={request_id}] "
                    f"Cleanup complete signaled for {stream_id}"
                )
            else:
                logger.warning(
                    f"[worker] [request_id={request_id}] "
                    f"Stream {stream_id} not in registry during cleanup signal"
                )

    async def _run_streaming_inference(
        self,
        stream_id: str,
        queue: Any,
        cancellation_event: asyncio.Event,
        prompt: str | None,
        messages: list[dict[str, str]] | None,
        parameters: dict[str, Any],
        request_id: str,
        reset_idle: Callable[[], None] = lambda: None,
    ) -> None:
        """Run the actual streaming inference (called after slot acquired)."""
        # Filter out metadata that shouldn't be passed to engine
        clean_params = {
            k: v
            for k, v in parameters.items()
            if k not in ["timeout_hint", "correlation_id", "_request_id", "worker_id"]
        }

        # Prepare data for engine
        if prompt is not None:
            data = {"prompt": prompt, **clean_params}
        else:
            data = {"messages": messages, **clean_params}

        logger.info(
            f"🔧 [worker] [request_id={request_id}] Starting streaming for {stream_id}"
        )

        chunk_count = 0
        try:
            async for chunk in self.engine.generate_stream(
                data, cancellation_event=cancellation_event
            ):
                if cancellation_event.is_set():
                    logger.info(
                        f"🛑 [worker] [request_id={request_id}] Stream {stream_id} "
                        "cancelled during generation"
                    )
                    error_frame = {
                        "t": "err",
                        "code": "CANCELLED",
                        "message": "Stream cancelled (idle timeout or external)",
                        "source": "stream",
                        "data": {},
                    }
                    try:
                        await queue.put(error_frame)
                    except Exception as e:
                        logger.warning(f"Failed to enqueue cancellation frame: {e}")
                    return

                chunk_count += 1
                reset_idle()

                # Extract content from OpenAI-format chunk
                # Engine returns: {"choices": [{"delta": {"content": "..."}}]} for chat
                # or {"choices": [{"text": "..."}]} for completions
                content = ""
                if isinstance(chunk, dict) and chunk.get("choices"):
                    choice = chunk["choices"][0]
                    # Try chat format first (delta.content)
                    delta = choice.get("delta", {})
                    if "content" in delta:
                        content = delta["content"] or ""
                    # Fall back to completion format (text)
                    elif "text" in choice:
                        content = choice["text"] or ""

                # Format as SSE token frame
                frame = {
                    "t": "token",
                    "i": chunk_count - 1,
                    "txt": content,
                }

                # Track token count (approximate - count words as tokens)
                if content:
                    token_count = len(content.split())
                    if token_count > 0:
                        from universal_protocol.observability import (
                            increment_stream_tokens,
                        )

                        increment_stream_tokens(stream_id, token_count)

                # Additional fields from chunk if available
                if isinstance(chunk, dict) and chunk.get("choices"):
                    finish_reason = chunk["choices"][0].get("finish_reason")
                    if finish_reason:
                        frame["finish_reason"] = finish_reason

                # Enqueue frame (no backpressure constraints)
                try:
                    await queue.put(frame)
                except Exception as e:
                    logger.error(
                        f"❌ [worker] [request_id={request_id}] "
                        f"Failed to enqueue frame: {e}"
                    )
                    error_frame = {
                        "t": "err",
                        "code": "STREAM_ERROR",
                        "message": f"Failed to enqueue frame: {e}",
                        "source": "stream",
                        "data": {},
                    }
                    try:
                        await queue.put(error_frame, timeout_seconds=0.1)
                    except Exception:
                        pass
                    return  # Exit without sending done frame

        except asyncio.CancelledError:
            # Forced cancellation via task.cancel()
            logger.info(
                f"⚠️ [worker] [request_id={request_id}] Stream {stream_id} "
                "force-cancelled"
            )
            # Emit cancellation frame
            error_frame = {
                "t": "err",
                "code": "CANCELLED",
                "message": "Stream force-cancelled due to unresponsive generator",
                "source": "stream",
                "data": {},
            }
            try:
                await queue.put(error_frame)
            except Exception as e:
                logger.warning(f"Failed to enqueue force-cancellation frame: {e}")

            from universal_protocol.observability import end_stream

            end_stream(stream_id)
            raise  # Re-raise to complete task cancellation

        except Exception as e:
            # Engine raised exception before or during generation
            logger.error(
                f"❌ [worker] [request_id={request_id}] Engine error during "
                f"stream generation for {stream_id}: {e}"
            )

            engine_error = self._map_exception_to_engine_error(e)

            error_frame = {
                "t": "err",
                "code": engine_error.code,
                "message": engine_error.message,
                "source": "engine",
                "data": {},
            }

            try:
                await queue.put(error_frame)
            except Exception as queue_error:
                logger.error(
                    f"❌ [worker] [request_id={request_id}] "
                    f"Failed to enqueue error frame: {queue_error}"
                )

            from universal_protocol.observability import end_stream

            end_stream(stream_id)
            return

        logger.info(
            f"✅ [worker] [request_id={request_id}] Streaming completed for "
            f"{stream_id}: {chunk_count} chunks"
        )

        done_frame = {
            "t": "done",
            "usage": {
                "prompt_tokens": 0,  # TODO: Get actual counts from engine
                "completion_tokens": chunk_count,
                "total_tokens": chunk_count,
            },
        }

        try:
            logger.info(
                f"📤 [worker] [request_id={request_id}] "
                f"Sending done frame for {stream_id}"
            )
            await queue.put(done_frame)
        except Exception as e:
            logger.error(
                f"❌ [worker] [request_id={request_id}] "
                f"Failed to send done frame for {stream_id}: {e}"
            )

        from universal_protocol.observability import end_stream

        end_stream(stream_id)
