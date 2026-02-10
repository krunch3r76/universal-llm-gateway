"""Vision/multi-modal support for GGUF engine."""

from .config import VisionConfig
from .content_utils import (
    count_images,
    extract_text_content,
    has_images,
    is_multimodal_message,
    normalize_message_content,
)
from .handler_factory import create_vision_handler, get_recommended_n_ctx
from .registry import (
    VISION_MODEL_REGISTRY,
    VisionModelInfo,
    get_vision_model_info,
    is_vision_model,
    list_supported_vision_models,
)
from .types import (
    ContentPart,
    ImageContent,
    ImageURL,
    MessageList,
    MultiModalContent,
    MultiModalMessage,
    TextContent,
)

__all__ = [
    # Types
    "TextContent",
    "ImageURL",
    "ImageContent",
    "ContentPart",
    "MultiModalContent",
    "MultiModalMessage",
    "MessageList",
    # Registry
    "VisionModelInfo",
    "VISION_MODEL_REGISTRY",
    "get_vision_model_info",
    "is_vision_model",
    "list_supported_vision_models",
    # Config
    "VisionConfig",
    # Handler factory
    "create_vision_handler",
    "get_recommended_n_ctx",
    # Content utilities
    "is_multimodal_message",
    "has_images",
    "count_images",
    "extract_text_content",
    "normalize_message_content",
]
