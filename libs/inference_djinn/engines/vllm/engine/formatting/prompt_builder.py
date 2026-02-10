"""
VLLM engine prompt building operations.

Handles prompt formatting and message-to-prompt conversion using chat templates.
"""

from universal_logging import get_logger
from typing import Any

logger = get_logger(__name__)


class VLLMPromptBuilder:
    """Handles prompt building and formatting for VLLM engine."""

    def __init__(self, engine_instance: Any):
        """
        Initialize prompt builder with reference to engine instance.

        Args:
            engine_instance: The VLLMEngine instance to operate on
        """
        self.engine = engine_instance

    def format_prompt_or_messages(self, data: dict[str, Any]) -> str:
        """
        Format prompt from data, handling both prompt strings and messages.

        Args:
            data: Request data containing either 'prompt' or 'messages'

        Returns:
            Formatted prompt string ready for inference

        Raises:
            ValueError: If neither prompt nor messages provided, or if empty
        """
        # Extract prompt or handle messages
        prompt = self.engine._extract_prompt(data)
        if prompt is None:
            # Handle messages format
            if "messages" in data:
                messages = data["messages"]
                if not isinstance(messages, list) or not messages:
                    raise ValueError("'messages' field must be a non-empty list")

                # Apply chat template if available
                if (
                    self.engine.tokenizer
                    and hasattr(self.engine.tokenizer, "apply_chat_template")
                    and self.engine.tokenizer.chat_template
                ):
                    prompt = self.engine.tokenizer.apply_chat_template(
                        messages, tokenize=False, add_generation_prompt=True
                    )
                else:
                    # Fallback to simple concatenation
                    prompt = "\n".join(msg.get("content", "") for msg in messages)
            else:
                raise ValueError("No prompt or messages provided")
        elif not prompt:
            raise ValueError("Empty prompt provided")

        return prompt
