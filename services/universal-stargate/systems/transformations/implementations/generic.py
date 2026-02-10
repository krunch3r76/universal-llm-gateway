"""Generic prompt extraction for simple transformations."""

from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


def transform_generic_prompt(
    messages: list[dict[str, Any]], settings: dict[str, Any]
) -> str:
    """
    Generic transformation: extract last user message content.

    Fallback for models needing prompt format without specific transformation.

    Args:
        messages: List of message dicts
        settings: Transformation settings (unused for generic)

    Returns:
        Prompt string from last user message
    """
    if not messages:
        return ""

    # Find last user message
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                logger.info(f"  → Extracted last user message ({len(content)} chars)")
                return content
            logger.warning(f"Non-string content in user message: {type(content)}")
            return str(content)

    logger.warning("No user message found, returning empty prompt")
    return ""
