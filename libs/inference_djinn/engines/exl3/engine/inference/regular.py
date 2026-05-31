"""
ExLlamaV3 engine regular (non-streaming) inference operations.

Handles non-streaming completion generation.
"""

import asyncio
import time
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


class ExLlamaV3RegularInference:
    """Handles regular (non-streaming) inference for ExLlamaV3 engine."""

    def __init__(self, engine_instance: Any):
        """
        Initialize regular inference with reference to engine instance.

        Args:
            engine_instance: The ExLlamaV3Engine instance to operate on
        """
        self.engine = engine_instance

    def _format_prompt_or_messages(
        self, prompt: str = None, messages: list = None
    ) -> str:
        """Format prompt from prompt string or messages."""
        if prompt is not None:
            return prompt
        else:
            # Simple message formatting fallback (extract last user message)
            if not messages:
                return ""
            else:
                user_messages = [msg for msg in messages if msg.get("role") == "user"]
                if user_messages:
                    return user_messages[-1].get("content", "")
                else:
                    return "\n".join(msg.get("content", "") for msg in messages)

    def _create_generation_settings(
        self, generation_params: dict[str, Any]
    ) -> tuple[dict[str, Any], int]:
        """Create ExLlamaV3 generation settings from parameters."""
        gen_settings = {}

        # Apply generation parameters to settings
        if "temperature" in generation_params:
            gen_settings["temperature"] = generation_params["temperature"]
        if "top_p" in generation_params:
            gen_settings["top_p"] = generation_params["top_p"]
        if "top_k" in generation_params:
            gen_settings["top_k"] = generation_params["top_k"]
        if "repetition_penalty" in generation_params:
            gen_settings["repetition_penalty"] = generation_params["repetition_penalty"]

        # Get number of tokens to generate
        max_new_tokens = generation_params.get("max_tokens", 50)

        # Handle stop sequences
        stop_sequences = generation_params.get("stop", [])
        if isinstance(stop_sequences, str):
            stop_sequences = [stop_sequences]
        if stop_sequences:
            gen_settings["stop_sequences"] = stop_sequences

        return gen_settings, max_new_tokens

    async def generate(
        self, data: dict[str, Any], cancellation_event: asyncio.Event | None = None
    ) -> dict[str, Any]:
        """
        Generate response using ExLlamaV3 with provided parameters only.

        Args:
            data: Request data with prompt/messages and generation params
            cancellation_event: Optional event to signal cancellation (currently unused for ExLlamaV3)

        Returns:
            OpenAI-compliant completion response

        Note:
            Cancellation support for ExLlamaV3 is not yet implemented.
        """
        if not self.engine.loaded:
            raise RuntimeError("Model not loaded")

        try:
            generation_params = self.engine._get_generation_params(data)

            # Extract prompt or messages from request using base class method
            prompt = self.engine._extract_prompt(data)
            messages = data.get("messages", []) if prompt is None else None

            start_time = time.time()

            # Determine the final prompt to use
            final_prompt = self._format_prompt_or_messages(prompt, messages)

            # Create generation settings
            gen_settings, max_new_tokens = self._create_generation_settings(
                generation_params
            )

            # Generate response using ExLlamaV3 generator
            output = await asyncio.to_thread(
                self.engine._generate_with_exllamav3,
                final_prompt,
                max_new_tokens,
                gen_settings,
            )

            generation_time = time.time() - start_time

            # Calculate token usage
            prompt_tokens = await asyncio.to_thread(
                self.engine._count_tokens, final_prompt
            )
            completion_tokens = await asyncio.to_thread(
                self.engine._count_tokens, output
            )

            return {
                "content": output,
                "finish_reason": "stop",  # ExLlamaV3 provides better finish reason support
                "usage": self.engine._create_usage_stats(
                    prompt_tokens, completion_tokens
                ),
                "model_id": self.engine._get_model_name(),
                "generation_time": generation_time,
            }

        except Exception as e:
            raise RuntimeError(f"ExLlamaV3 generation failed: {e}") from e
