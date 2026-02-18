"""Utilities for handling multi-modal message content."""

from .types import (
    ContentPart,
    MessageList,
    MultiModalContent,
    MultiModalMessage,
)


def is_multimodal_message(message: MultiModalMessage) -> bool:
    """Check if a message contains multi-modal content (images)."""
    content = message.get("content")
    if isinstance(content, str):
        return False
    if isinstance(content, list):
        return any(
            isinstance(part, dict) and part.get("type") == "image_url"
            for part in content
        )
    return False


def has_images(messages: MessageList) -> bool:
    """Check if any message in the list contains images."""
    return any(is_multimodal_message(msg) for msg in messages)


def count_images(messages: MessageList) -> int:
    """Count total images across all messages."""
    count = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            count += sum(
                1
                for part in content
                if isinstance(part, dict) and part.get("type") == "image_url"
            )
    return count


def extract_text_content(message: MultiModalMessage) -> str:
    """Extract text content from a message, ignoring images."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = [
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        ]
        return " ".join(text_parts)
    return ""


def normalize_message_content(content: MultiModalContent) -> list[ContentPart]:
    """Normalize content to always be a list of content parts.

    Returns a new list to avoid shared mutations.
    """
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return list(content)
