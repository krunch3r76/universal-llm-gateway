"""
VLLM engine regular (non-streaming) inference operations.

Handles non-streaming completion generation with cancellation support.
"""

import asyncio
from universal_logging import get_logger
import time
from typing import Any

from vllm.utils import random_uuid

logger = get_logger(__name__)


class GenerationCancelled(RuntimeError):
    """Generation was cancelled by client request."""

    pass


class VLLMRegularInference:
    """Handles regular (non-streaming) inference for VLLM engine."""

    def __init__(self, engine_instance: Any):
        """
        Initialize regular inference with reference to engine instance.

        Args:
            engine_instance: The VLLMEngine instance to operate on
        """
        self.engine = engine_instance

    async def generate(
        self, data: dict[str, Any], cancellation_event: asyncio.Event | None = None
    ) -> dict[str, Any]:
        """
        Generate response using vLLM with optional cancellation support.

        Args:
            data: Request data with prompt/messages and generation params
            cancellation_event: Optional event to signal cancellation

        Returns:
            OpenAI-compliant completion response

        Raises:
            RuntimeError: If model not loaded or generation fails
        """
        if not self.engine.loaded:
            raise RuntimeError("Model not loaded. Call load() first.")

        # Generate unique request ID for this inference
        request_id = random_uuid()

        # Initialize cancellation monitor outside try block to ensure cleanup
        cancellation_monitor = None
        try:
            # Format prompt from data
            prompt = self.engine._prompt_builder.format_prompt_or_messages(data)

            # Extract generation parameters
            generation_params = self.engine._get_generation_params(data)

            # Create sampling parameters
            sampling_params = self.engine._param_builder.create_sampling_params(
                generation_params
            )

            # Start cancellation monitor if event provided
            if cancellation_event:

                async def monitor_cancellation():
                    """Monitor cancellation event and abort vLLM request."""
                    await cancellation_event.wait()
                    logger.info(
                        f"🛑 Cancellation requested for vLLM request {request_id}"
                    )
                    try:
                        # Abort the vLLM request
                        await self.engine.llm.abort(request_id)
                        logger.info(f"✅ vLLM request {request_id} aborted")
                    except Exception as e:
                        logger.warning(
                            f"Failed to abort vLLM request {request_id}: {e}"
                        )

                cancellation_monitor = asyncio.create_task(monitor_cancellation())

            # Generate response using AsyncLLMEngine
            results_generator = self.engine.llm.generate(
                prompt, sampling_params, request_id
            )

            # Collect all outputs for non-streaming
            final_output = None
            try:
                async for request_output in results_generator:
                    # Check if cancelled
                    if cancellation_event and cancellation_event.is_set():
                        logger.info(f"🛑 Generation cancelled for request {request_id}")
                        raise asyncio.CancelledError("Generation cancelled by client")

                    final_output = request_output

            except asyncio.CancelledError:
                # Ensure abort is called
                try:
                    await self.engine.llm.abort(request_id)
                except Exception:
                    pass  # Already aborted or doesn't exist
                raise GenerationCancelled("Generation cancelled") from None

            finally:
                # Cancel monitor task if still running
                if cancellation_monitor and not cancellation_monitor.done():
                    cancellation_monitor.cancel()
                    try:
                        await cancellation_monitor
                    except asyncio.CancelledError:
                        pass

            if not final_output or not final_output.outputs:
                raise RuntimeError("No output generated")

            # Extract response
            output = final_output.outputs[0]
            response_text = output.text

            # Count tokens if tokenizer is available
            prompt_tokens = 0
            completion_tokens = 0

            if self.engine.tokenizer:
                try:
                    prompt_tokens = len(self.engine.tokenizer.encode(prompt))
                    completion_tokens = len(self.engine.tokenizer.encode(response_text))
                except Exception as e:
                    logger.warning(f"Could not count tokens: {e}")

            response_data = {
                "id": f"chatcmpl-{int(time.time() * 1000)}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": self.engine._get_model_name(),
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": response_text},
                        "finish_reason": "stop",
                    }
                ],
                "usage": self.engine._create_usage_stats(
                    prompt_tokens, completion_tokens
                ),
            }

            return response_data

        except GenerationCancelled:
            logger.info(f"Generation cancelled for request {request_id}")
            raise
        except Exception as e:
            logger.error(f"Generation error: {type(e).__name__}: {e}")
            raise RuntimeError(f"vLLM generation failed: {e}") from e
        finally:
            # Ensure cancellation monitor is always cleaned up, even if error occurs
            # before inner try block (e.g., at line 90)
            if cancellation_monitor and not cancellation_monitor.done():
                cancellation_monitor.cancel()
                try:
                    await cancellation_monitor
                except asyncio.CancelledError:
                    pass
