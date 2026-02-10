"""Schema-to-path mapping for catalog split."""

from typing import Any

# Schema to domain/engine mapping
SCHEMA_TO_PATH = {
    "llama-cpp": "text_llm/llama-cpp",
    "vllm": "text_llm/vllm",
    "exllamav3": "text_llm/exllamav3",
    "faster-whisper": "audio/whisper",
    "ctranslate2": "translation/ctranslate2",
    "diffusers": "graphics/diffusers",
}

VISUAL_FAMILIES = {"llava", "llava-next"}


def determine_model_path(model_id: str, model_entry: dict[str, Any]) -> str:
    """
    Determine domain/engine path for model.

    Returns:
        Relative path like "text_llm/llama-cpp"

    Raises:
        ValueError: If schema missing or unknown
    """
    schema = model_entry.get("schema")
    if not schema:
        raise ValueError(f"Model '{model_id}' missing schema field")

    # Check for visual models
    family = model_entry.get("metadata", {}).get("family", "").lower()
    if family in VISUAL_FAMILIES:
        return "visual/llava"

    path = SCHEMA_TO_PATH.get(schema)
    if not path:
        raise ValueError(
            f"Model '{model_id}' has unknown schema '{schema}'. "
            f"Valid schemas: {list(SCHEMA_TO_PATH.keys())}"
        )

    return path
