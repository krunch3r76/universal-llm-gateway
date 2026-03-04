"""Schema-to-path mapping for catalog split."""

from typing import Any

SCHEMA_TO_PATH = {
    "llama-cpp": "text_llm/llama-cpp",
    "vllm": "text_llm/vllm",
    "exllamav3": "text_llm/exllamav3",
    "faster-whisper": "audio/whisper",
    "ctranslate2": "translation/ctranslate2",
    "diffusers": "graphics/diffusers",
}

# ∀ visual models (capabilities.modalities.input contains "vision"): routed by engine
VISUAL_SCHEMA_TO_PATH = {
    "llama-cpp": "visual/llama-cpp",
    "vllm": "visual/vllm",
}

# ∀ embedding models (loader.embedding=True): separate API, no KV cache
EMBEDDING_SCHEMA_TO_PATH = {
    "llama-cpp": "embedding/llama-cpp",
    "vllm": "embedding/vllm",
}


def determine_model_path(model_id: str, model_entry: dict[str, Any]) -> str:
    """
    Determine domain/engine path for model.

    ∀ model: is_vision ⟹ visual/{engine}
           | embedding ⟹ embedding/{engine}
           | else ⟹ {domain}/{engine}

    Returns:
        Relative path like "visual/llama-cpp" or "embedding/vllm" or "text_llm/vllm"

    Raises:
        ValueError: If schema missing, unknown, or domain schema unsupported
    """
    schema = model_entry.get("schema")
    if not schema:
        raise ValueError(f"Model '{model_id}' missing schema field")

    caps = model_entry.get("metadata", {}).get("capabilities", {})
    modalities = caps.get("modalities", {})
    is_vision = "vision" in modalities.get("input", [])
    if is_vision:
        path = VISUAL_SCHEMA_TO_PATH.get(schema)
        if not path:
            raise ValueError(
                f"Model '{model_id}' is a vision model but schema '{schema}' "
                f"has no visual path. Supported: {list(VISUAL_SCHEMA_TO_PATH.keys())}"
            )
        return path

    is_embedding = model_entry.get("loader", {}).get("embedding") is True
    if is_embedding:
        path = EMBEDDING_SCHEMA_TO_PATH.get(schema)
        if not path:
            raise ValueError(
                f"Model '{model_id}' is an embedding model but schema '{schema}' "
                f"has no embedding path. "
                f"Supported: {list(EMBEDDING_SCHEMA_TO_PATH.keys())}"
            )
        return path

    path = SCHEMA_TO_PATH.get(schema)
    if not path:
        raise ValueError(
            f"Model '{model_id}' has unknown schema '{schema}'. "
            f"Valid schemas: {list(SCHEMA_TO_PATH.keys())}"
        )
    return path
