"""
GGUF engine token counting operations.

Handles token counting for formatted prompt strings and multi-modal messages.
Includes image token estimation for vision models.
"""

import asyncio
from universal_logging import get_logger
from typing import Any

from inference_djinn.utils.types import TokenCountResult

from .vision.content_utils import count_images, extract_text_content
from .vision.types import MessageList

logger = get_logger(__name__)

# Try to import llama-cpp-python
try:
    from llama_cpp import Llama

    llama_cpp_available = True
except ImportError:
    logger.warning("llama-cpp-python not available - token counting will fail")
    llama_cpp_available = False
    Llama = None


class GGUFTokenCounter:
    """Handles token counting operations for GGUF engine."""

    def __init__(self, engine_instance: Any):
        """
        Initialize token counter with reference to engine instance.

        Args:
            engine_instance: The GGUFEngine instance to operate on
        """
        self.engine = engine_instance

    @staticmethod
    def _format_messages_simple(messages: MessageList) -> str:
        """
        Format messages into a simple prompt string for token counting.
        Extracts text content only (images handled separately via image token estimation).

        Args:
            messages: List of chat messages (may contain multi-modal content)

        Returns:
            Formatted prompt string with text content only
        """
        formatted = ""
        for message in messages:
            role = message.get("role", "user")
            # Use extract_text_content to handle both string and list content
            content = extract_text_content(message)

            if role == "system":
                formatted += f"System: {content}\n\n"
            elif role == "user":
                formatted += f"User: {content}\n"
            elif role == "assistant":
                formatted += f"Assistant: {content}\n"

        return formatted

    async def count_tokens_for_messages(
        self,
        messages_or_prompt: MessageList | str,
        use_cpu: bool = True,  # Kept for API compatibility, but ignored
        context_length: int | None = None,
    ) -> TokenCountResult:
        """
        Count tokens for messages or prompt, including image token estimates.

        Supports both input formats:
        - str: Formatted prompt (fast path using direct tokenization)
        - list: Chat messages (may contain multi-modal content with images)

        For vision models, image tokens are estimated using registry values.

        Args:
            messages_or_prompt: Either formatted prompt or message list
            use_cpu: Kept for API compatibility, but ignored
            context_length: Context length for validation

        Returns:
            TokenCountResult with count, method, and success status
        """
        import time

        if not self.engine.loaded or not self.engine.llama_model:
            raise RuntimeError("Model must be loaded before counting tokens")

        start_time = time.time()
        image_tokens = 0

        # Handle image token estimation for vision models
        if isinstance(messages_or_prompt, list) and self.engine.supports_vision:
            image_count = count_images(messages_or_prompt)
            if image_count > 0:
                # Get tokens per image from vision config
                vision_info = self.engine.get_vision_info()
                tokens_per_image = (
                    vision_info.get("tokens_per_image", 2048) if vision_info else 2048
                )
                image_tokens = image_count * tokens_per_image
                logger.info(
                    f"🖼️ [GGUF] {image_count} image(s) × {tokens_per_image} = "
                    f"{image_tokens} image tokens"
                )

        # Convert input to prompt string
        if isinstance(messages_or_prompt, str):
            prompt = messages_or_prompt
            input_type = "prompt"
            logger.info(
                f"🔍 [GGUF] Token counting for prompt string ({len(prompt)} chars)"
            )
        elif isinstance(messages_or_prompt, list):
            prompt = self._format_messages_simple(messages_or_prompt)
            input_type = "messages"
            logger.info(
                f"🔍 [GGUF] Token counting for {len(messages_or_prompt)} messages"
            )
        else:
            raise ValueError(f"Expected str or list, got {type(messages_or_prompt)}")

        if not prompt and image_tokens == 0:
            return TokenCountResult(
                tokens=0, method=f"gguf_tokenizer_{input_type}", success=True
            )

        # Tokenize text content
        try:
            text_tokens = 0
            if prompt:
                tokens = await asyncio.wait_for(
                    asyncio.to_thread(
                        self.engine.llama_model.tokenize, prompt.encode("utf-8")
                    ),
                    timeout=10.0,
                )
                text_tokens = len(tokens)

            total_tokens = text_tokens + image_tokens
            time_taken = time.time() - start_time

            logger.info(
                f"🔍 [GGUF] Total: {total_tokens} tokens "
                f"(text: {text_tokens}, images: {image_tokens}) in {time_taken:.3f}s"
            )

            method_suffix = "_vision" if image_tokens > 0 else ""
            return TokenCountResult(
                tokens=total_tokens,
                method=f"gguf_tokenizer_{input_type}{method_suffix}",
                success=True,
                time_taken=time_taken,
            )

        except TimeoutError:
            # Timeout fallback with image tokens included
            estimated_text = max(1, len(prompt) // 4) if prompt else 0
            estimated_tokens = estimated_text + image_tokens
            time_taken = time.time() - start_time
            logger.warning(
                f"Token counting timed out, using approximation: {estimated_tokens}"
            )

            return TokenCountResult(
                tokens=estimated_tokens,
                method=f"gguf_tokenizer_{input_type}_timeout",
                success=True,
                time_taken=time_taken,
                error="Token counting timed out, using approximation",
            )

        except Exception as e:
            time_taken = time.time() - start_time
            logger.error(f"Token counting failed: {e}")
            return TokenCountResult(
                tokens=0,
                method=f"gguf_tokenizer_{input_type}_error",
                success=False,
                error=str(e)[:500],
                time_taken=time_taken,
            )
