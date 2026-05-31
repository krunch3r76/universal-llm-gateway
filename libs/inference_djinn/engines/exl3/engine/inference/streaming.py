"""
ExLlamaV3 engine streaming inference operations.

Handles streaming completion generation.
"""

import time
from collections.abc import AsyncGenerator
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


class ExLlamaV3StreamingInference:
    """Handles streaming inference for ExLlamaV3 engine."""

    def __init__(self, engine_instance: Any, regular_inference: Any):
        """
        Initialize streaming inference with reference to engine instance.

        Args:
            engine_instance: The ExLlamaV3Engine instance to operate on
            regular_inference: The ExLlamaV3RegularInference instance
        """
        self.engine = engine_instance
        self.regular = regular_inference

    async def generate_stream(
        self, data: dict[str, Any], cancellation_event: Any | None = None
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Generate streaming response using ExLlamaV3 with provided parameters only"""
        if not self.engine.loaded:
            raise RuntimeError("Model not loaded")

        try:
            generation_params = self.engine._get_generation_params(data)

            # Extract prompt or messages from request using base class method
            prompt = self.engine._extract_prompt(data)
            messages = data.get("messages", []) if prompt is None else None

            start_time = time.time()

            # Determine the final prompt to use
            final_prompt = self.regular._format_prompt_or_messages(prompt, messages)

            # Create generation settings
            gen_settings, max_new_tokens = self.regular._create_generation_settings(
                generation_params
            )

            # Count prompt tokens for accurate usage stats
            prompt_tokens = 0
            try:
                prompt_tokens = self.engine._count_tokens(final_prompt)
            except Exception as e:
                logger.warning(f"Could not get prompt token count: {e}")

            # Generate response using ExLlamaV3 generator with streaming
            total_completion_tokens = 0
            async for chunk in self.engine._generate_with_exllamav3_stream(
                final_prompt, max_new_tokens, gen_settings
            ):
                # Update completion token count from chunk
                if chunk.get("type") == "token":
                    total_completion_tokens = chunk.get(
                        "token_count", total_completion_tokens
                    )
                yield chunk

            # Send final chunk with generation time
            yield {
                "type": "finish",
                "content": "",
                "finish_reason": "stop",
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": total_completion_tokens,
                    "total_tokens": prompt_tokens + total_completion_tokens,
                },
                "model_id": self.engine._get_model_name(),
                "total_generation_time": time.time() - start_time,
            }

        except Exception as e:
            raise RuntimeError(f"ExLlamaV3 streaming failed: {e}") from e
