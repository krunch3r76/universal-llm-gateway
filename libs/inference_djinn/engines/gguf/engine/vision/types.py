"""Type definitions for multi-modal message content."""

from typing import Literal, TypedDict


class TextContent(TypedDict):
    """Text content part in a multi-modal message."""

    type: Literal["text"]
    text: str


class ImageURL(TypedDict):
    """Image URL specification."""

    url: str  # file://, data:image/..., or https://


class ImageContent(TypedDict):
    """Image content part in a multi-modal message."""

    type: Literal["image_url"]
    image_url: ImageURL


# Union type for content parts
ContentPart = TextContent | ImageContent

# Multi-modal message: content can be string OR list of content parts
MultiModalContent = str | list[ContentPart]


class MultiModalMessage(TypedDict):
    """A message that may contain multi-modal content."""

    role: Literal["system", "user", "assistant"]
    content: MultiModalContent


# Type alias for message lists
MessageList = list[MultiModalMessage]
