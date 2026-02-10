"""
VLLM engine streaming inference operations.

Handles streaming completion generation.
"""

from universal_logging import get_logger
from collections.abc import AsyncGenerator
from typing import Any

from vllm.utils import random_uuid

from inference_djinn.utils.streaming_core import emit_openai_stream

logger = get_logger(__name__)


class VLLMStreamingInference:
    """Handles streaming inference for VLLM engine."""

    def __init__(self, engine_instance: Any):
        """
        Initialize streaming inference with reference to engine instance.

        Args:
            engine_instance: The VLLMEngine instance to operate on
        """
        self.engine = engine_instance

    async def generate_stream(
        self, data: dict[str, Any], cancellation_event: Any | None = None
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        Generate streaming response using vLLM AsyncLLMEngine.

        Args:
            data: Request data with prompt/messages and generation params
            cancellation_event: Optional event to signal cancellation of streaming.
                When set, streaming stops gracefully after the current chunk.

        Yields:
            OpenAI-compliant streaming chunks

        Raises:
            RuntimeError: If streaming fails (never yields error chunks)
        """
        if not self.engine.loaded:
            raise RuntimeError("Model not loaded")

        try:
            # Format prompt from data
            prompt = self.engine._prompt_builder.format_prompt_or_messages(data)

            # Extract generation parameters
            generation_params = self.engine._get_generation_params(data)

            # Create sampling parameters
            sampling_params = self.engine._param_builder.create_sampling_params(
                generation_params
            )

            # Generate streaming response using AsyncLLMEngine
            request_id = random_uuid()
            results_generator = self.engine.llm.generate(
                prompt, sampling_params, request_id
            )

            # Convert vLLM chunks to OpenAI format with cancellation support
            async def vllm_chunk_iterator():
                """Convert vLLM chunks to standard format for unified core."""
                previous_text = ""

                async for request_output in results_generator:
                    if request_output.finished:
                        # Final chunk - yield empty content to signal completion
                        yield {"choices": [{"finish_reason": "stop"}]}
                        break
                    else:
                        # Content chunk - extract new text
                        if request_output.outputs:
                            output = request_output.outputs[0]
                            if output.text:
                                current_text = output.text

                                # Calculate new text by removing previous text
                                if current_text.startswith(previous_text):
                                    new_text = current_text[len(previous_text) :]
                                else:
                                    # Fallback: use difference if text doesn't start with previous
                                    if len(current_text) > len(previous_text):
                                        new_text = current_text[len(previous_text) :]
                                    else:
                                        new_text = current_text
                                        logger.warning("Text alignment issue detected")

                                if new_text:
                                    yield {
                                        "choices": [{"delta": {"content": new_text}}]
                                    }
                                    previous_text = current_text

            # Use unified streaming core
            async for chunk in emit_openai_stream(
                vllm_chunk_iterator(),
                self.engine._get_model_name(),
                is_chat=True,  # vLLM uses chat format
                cancellation_event=cancellation_event,
            ):
                yield chunk

        except Exception as e:
            logger.error(f"Streaming generation error: {type(e).__name__}: {e}")
            raise RuntimeError(f"vLLM streaming failed: {e}") from e
