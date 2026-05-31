"""
GGUF engine prompt building operations.

Handles prompt formatting and message-to-prompt conversion.
"""

from typing import Any

from universal_logging import get_logger

from ..vision.content_utils import extract_text_content
from ..vision.types import MessageList

logger = get_logger(__name__)


class GGUFPromptBuilder:
    """Handles prompt building and formatting for GGUF engine."""

    def __init__(self, engine_instance: Any):
        """
        Initialize prompt builder with reference to engine instance.

        Args:
            engine_instance: The GGUFEngine instance to operate on
        """
        self.engine = engine_instance

    def build_formatted_prompt(
        self, messages: MessageList | None, prompt: str | None
    ) -> str:
        """
        Build canonical formatted prompt for both messages and prompt paths.

        Args:
            messages: Chat messages to format via template
            prompt: Pre-formatted prompt string (or None)

        Returns:
            Formatted prompt string ready for tokenization/generation

        Raises:
            ValueError: If both or neither messages/prompt provided
        """
        if messages is not None and prompt is not None:
            raise ValueError("Cannot provide both messages and prompt - choose one")

        if messages is None and prompt is None:
            raise ValueError("Must provide either messages or prompt")

        if messages is not None:
            # Messages path: use simple formatting since we can't extract formatted prompt from create_chat_completion
            if not self.engine._chat_template_available:
                raise ValueError(
                    "Chat template not available for this model. Use prompt-based requests instead."
                )

            try:
                # Use simple message formatting since we can't extract the exact formatted prompt
                # from create_chat_completion response
                formatted_prompt = self.format_messages_simple(messages)

                if self.engine.debug_token_level:
                    logger.debug(
                        f"Formatted prompt (first 200 chars): {repr(formatted_prompt[:200])}"
                    )

                return formatted_prompt

            except Exception as e:
                error_msg = str(e)
                logger.error(f"Failed to format messages: {error_msg}")
                raise RuntimeError(f"Message formatting failed: {error_msg}")

        else:
            # Prompt path: return as-is (assume already properly formatted)
            if self.engine.debug_token_level:
                logger.debug(
                    f"Using provided prompt (first 200 chars): {repr(prompt[:200])}"
                )

            return prompt

    def format_messages_simple(self, messages: MessageList) -> str:
        """
        Simple message formatting fallback when chat template is not available.
        Extracts text content from multi-modal messages (images are handled separately).

        Args:
            messages: List of chat messages (may contain multi-modal content)

        Returns:
            Simple formatted prompt string with text content only
        """
        formatted_parts = []

        for message in messages:
            role = message.get("role", "user")
            # Use extract_text_content to handle both string and list content
            content = extract_text_content(message)

            if role == "system":
                formatted_parts.append(f"System: {content}")
            elif role == "user":
                formatted_parts.append(f"Human: {content}")
            elif role == "assistant":
                formatted_parts.append(f"Assistant: {content}")
            else:
                formatted_parts.append(f"{role.title()}: {content}")

        # Add generation prompt
        formatted_prompt = "\n".join(formatted_parts) + "\nAssistant:"

        return formatted_prompt
