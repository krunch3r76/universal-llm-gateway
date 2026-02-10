"""
GGUF engine streaming inference operations.

Handles streaming completion generation for both chat and prompt inputs.
Supports multi-modal (vision) messages via MessageList type.

Cancellation Support:
    Uses AbortController for C-level cancellation of llama.cpp operations.
    This enables cancellation during prefill (before first token), not just
    between token generations.
"""

import asyncio
import contextlib
import time
from collections.abc import AsyncGenerator
from typing import Any

from universal_logging import get_logger

from inference_djinn.utils.streaming_core import emit_openai_stream, iterate_blocking

from ..vision.types import MessageList
from .abort_controller import AbortController
from .parent_monitor import monitor_parent_death

logger = get_logger(__name__)


class GGUFStreamingInference:
    """Handles streaming inference for GGUF engine with cancellation support."""

    def __init__(self, engine_instance: Any, regular_inference: Any):
        """
        Initialize streaming inference with reference to engine instance.

        Args:
            engine_instance: The GGUFEngine instance to operate on
            regular_inference: The GGUFRegularInference instance for shared methods
        """
        self.engine = engine_instance
        self.regular = regular_inference

    async def generate_stream(
        self, data: dict[str, Any], cancellation_event: asyncio.Event | None = None
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        Generate streaming response using GGUF model with unified streaming approach.

        Args:
            data: Request data containing:
                - prompt: Optional client-formatted prompt string
                - messages: Optional list of chat messages
            cancellation_event: Optional event to signal cancellation of streaming.
                When set, triggers llama.cpp abort callback for immediate C-level
                cancellation. Works during prefill phase (before first token).

        Yields:
            Dict containing response chunks in OpenAI format

        Raises:
            RuntimeError: If generation fails at any point
            ValueError: If input data is invalid
            MemoryError: If out of memory during generation
            AttributeError: If internal method call fails (wrapped as RuntimeError)

        Note:
            This method will NEVER yield error dictionaries.
            All errors are raised as exceptions.
        """
        if not self.engine.loaded:
            raise RuntimeError("Model not loaded")

        # Create abort controller for C-level cancellation
        # This enables cancellation during prefill (before first token)
        abort_controller = AbortController(self.engine.llama_model)
        try:
            abort_controller.arm()

            # Cancellation event monitor (optional)
            cancellation_monitor: asyncio.Task[None] | None = None
            if cancellation_event:

                async def monitor_cancellation() -> None:
                    await cancellation_event.wait()
                    abort_controller.trigger()
                    logger.info("🛑 [streaming] Abort triggered via cancellation event")

                cancellation_monitor = asyncio.create_task(monitor_cancellation())

            # Parent death monitor (always enabled)
            parent_monitor: asyncio.Task[None] | None = None
            parent_monitor = asyncio.create_task(
                monitor_parent_death(
                    abort_trigger=abort_controller.trigger,
                    cancellation_event=cancellation_event,
                )
            )

            try:
                generation_params = self.engine._get_generation_params(data)

                # Extract prompt or messages from request
                prompt = self.engine._extract_prompt(data)
                messages = data.get("messages", []) if prompt is None else None

                # Log all arguments passed to the GGUF engine during inference
                logger.info("GGUF streaming inference request:")
                logger.info(f"  Model: {self.engine._get_model_name()}")

                if prompt is not None:
                    logger.info("  Prompt-based request (client-formatted)")
                    logger.info(f"  Prompt length: {len(prompt)} characters")
                else:
                    logger.info("  Messages-based request (chat template)")
                    logger.info(f"  Messages count: {len(messages)}")
                    if messages:
                        logger.info(
                            f"  First message role: {messages[0].get('role', 'unknown')}"
                        )
                        logger.info(
                            f"  Last message role: {messages[-1].get('role', 'unknown')}"
                        )

                if generation_params:
                    logger.info("  Generation parameters:")
                    for key, value in generation_params.items():
                        logger.info(f"    {key}: {value}")
                else:
                    logger.info("  Generation parameters: None (using defaults)")

                start_time = time.time()

                # Unified streaming approach
                async for chunk in self._unified_stream(
                    prompt, messages, generation_params, start_time, cancellation_event
                ):
                    chunk["timestamp"] = time.time()
                    yield chunk

            except Exception as e:
                logger.error(
                    f"Error during generation: {type(e).__name__}: {e}", exc_info=True
                )
                raise
            finally:
                # Cancel monitors
                if cancellation_monitor is not None:
                    cancellation_monitor.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await cancellation_monitor

                if parent_monitor is not None:
                    parent_monitor.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await parent_monitor
        finally:
            # CRITICAL: Always disarm abort callback to reset llama.cpp state
            abort_controller.disarm()

    async def _unified_stream(
        self,
        prompt: str | None,
        messages: MessageList | None,
        generation_params: dict[str, Any],
        start_time: float,
        cancellation_event: asyncio.Event | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        Unified streaming method that handles both prompt and messages.
        Always returns OpenAI-compliant format regardless of input type.

        Args:
            prompt: Optional client-formatted prompt string
            messages: Optional list of chat messages
            generation_params: Dictionary of generation parameters
            start_time: Start time for generation timing
            cancellation_event: Optional event to signal cancellation of streaming

        Yields:
            Dict containing response chunks in OpenAI format

        Raises:
            RuntimeError: If streaming generation fails at any point

        Note:
            This method will NEVER yield error dictionaries.
            All errors are raised as exceptions.
        """
        try:
            logger.debug("Using unified completion path for streaming")

            await self.engine._perform_warmup(
                is_streaming=True,
                messages=messages,
                prompt=prompt,
                request_max_tokens=generation_params.get("max_tokens"),
            )

            # Use unified completion for streaming generation
            try:
                final_gen_params = self.engine._build_generation_params(
                    generation_params, is_streaming=True
                )

                stream_gen = await self.regular._create_completion_unified(
                    messages, prompt, final_gen_params, is_streaming=True
                )

                is_chat = messages is not None
                logger.debug(
                    f"🔍 [GGUF] Streaming from llama-cpp-python, is_chat={is_chat}"
                )

                async for chunk in emit_openai_stream(
                    iterate_blocking(stream_gen),
                    self.engine._get_model_name(),
                    is_chat=is_chat,
                    cancellation_event=cancellation_event,
                ):
                    yield chunk
                return

            except Exception as e:
                error_msg = str(e)
                logger.error(
                    f"Unified streaming failed: {type(e).__name__}: {error_msg}"
                )
                raise RuntimeError(f"Unified streaming failed: {error_msg}") from e

        except Exception as e:
            error_msg = str(e)
            logger.error(
                f"Unified streaming failed: {type(e).__name__}: {error_msg}",
                exc_info=True,
            )
            raise
