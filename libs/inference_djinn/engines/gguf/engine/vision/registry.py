"""Registry of supported vision model handlers."""

from dataclasses import dataclass


@dataclass
class VisionModelInfo:
    """Information about a supported vision model."""

    handler_class_name: str  # e.g., "Qwen25VLChatHandler"
    chat_format: str  # e.g., "qwen2.5-vl"
    default_n_ctx: int  # Recommended context size
    tokens_per_image: int  # Estimated tokens per image embedding
    description: str


# Registry of supported vision models
# Key: architecture identifier (from GGUF metadata or config)
VISION_MODEL_REGISTRY: dict[str, VisionModelInfo] = {
    "qwen2_vl": VisionModelInfo(
        handler_class_name="Qwen25VLChatHandler",
        chat_format="qwen2.5-vl",
        default_n_ctx=8192,
        tokens_per_image=2048,
        description="Qwen2.5-VL: Strong multilingual OCR, Greek support",
    ),
    "llava_1_5": VisionModelInfo(
        handler_class_name="Llava15ChatHandler",
        chat_format="llava-1-5",
        default_n_ctx=4096,
        tokens_per_image=576,
        description="LLaVA 1.5: General-purpose vision-language",
    ),
    "llava_1_6": VisionModelInfo(
        handler_class_name="Llava16ChatHandler",
        chat_format="llava-1-6",
        default_n_ctx=8192,
        tokens_per_image=2880,
        description="LLaVA 1.6 (NeXT): High-res, better OCR",
    ),
    "minicpm_v": VisionModelInfo(
        handler_class_name="MiniCPMv26ChatHandler",
        chat_format="minicpm-v-2.6",
        default_n_ctx=4096,
        tokens_per_image=1024,
        description="MiniCPM-V 2.6: Efficient OCR, document understanding",
    ),
    "moondream": VisionModelInfo(
        handler_class_name="MoondreamChatHandler",
        chat_format="moondream2",
        default_n_ctx=2048,
        tokens_per_image=729,
        description="Moondream2: Lightweight, edge-friendly",
    ),
}


def get_vision_model_info(architecture: str) -> VisionModelInfo | None:
    """Look up vision model info by architecture."""
    return VISION_MODEL_REGISTRY.get(architecture)


def is_vision_model(architecture: str) -> bool:
    """Check if architecture is a known vision model."""
    return architecture in VISION_MODEL_REGISTRY


def list_supported_vision_models() -> list[str]:
    """List all supported vision model architectures."""
    return list(VISION_MODEL_REGISTRY.keys())
