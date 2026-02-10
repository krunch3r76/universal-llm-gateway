"""
Model utilities for the universal-llm-gateway.

Provides shared utility functions for model metadata and capabilities.
"""

from universal_logging import get_logger

from src.schemas.model_info import ChatTemplateInfo, InferenceEngineInfo, ModelInfo

logger = get_logger(__name__)


def is_token_counting_available(model_metadata: dict) -> bool:
    """
    Determine if token counting is available for a given model.

    Args:
        model_metadata: Model metadata dictionary

    Returns:
        True if token counting is supported for this model type
    """
    loader_type = model_metadata.get("loader_type", "").lower()

    # Token counting is available for all current loader types
    if loader_type in ["gguf", "awq", "gptq", "transformers_awq"]:
        return True

    # Default to True for new loader types (conservative approach)
    return True


def detect_model_type(
    model_id: str, model_config: dict | None = None, model_path: str | None = None
) -> str:
    """
    Detect model type using configuration or model file inspection.

    This function first checks YAML configuration, then falls back to
    inspecting the model file using inference_djinn if needed.

    Args:
        model_id: Model identifier
        model_config: Optional model configuration from model_loaders.yaml
        model_path: Optional path to model file (for inference_djinn inspection)

    Returns:
        Model type string (e.g., 'llama', 'mistral', 'qwen', 'default')
    """
    # Priority 1: Explicit type in YAML config (fastest)
    if model_config:
        model_info = model_config.get("info", {})

        # Check for explicit model_type
        if "model_type" in model_info:
            return model_info["model_type"]

        # Check for model family
        if "family" in model_info:
            return model_info["family"]

    # Priority 2: Use inference_djinn to inspect the model file (accurate)
    if model_path:
        try:
            from inference_djinn.utils.format_detector import ModelFormatDetector

            # Detect model format first
            format_type = ModelFormatDetector.detect_format(model_path)

            # Use appropriate inspector based on format
            if format_type == "gguf":
                from inference_djinn.engines.gguf.inspector import (
                    detect_model_type_from_metadata,
                    load_gguf_metadata,
                )

                meta = load_gguf_metadata(model_path)
                if meta:
                    return detect_model_type_from_metadata(meta)

            elif format_type in ["hf", "awq", "gptq"]:
                # For vLLM models, use vllm_model_analyzer detection if needed
                # This could be imported from scripts but it's designed for analysis
                # For now, fall back to name-based detection
                pass

        except Exception as e:
            logger.debug(f"Could not use inference_djinn for model type detection: {e}")

    # Priority 3: Fallback to simple name-based detection
    return "default"


def convert_metadata_to_model_info(model_metadata: dict) -> ModelInfo:
    """
    Convert model metadata dictionary to ModelInfo schema.

    This centralizes the conversion logic used across multiple endpoints,
    ensuring consistency and preventing bugs from duplicate code.

    Args:
        model_metadata: Raw model metadata dictionary

    Returns:
        ModelInfo: Validated ModelInfo schema object
    """
    # Convert nested objects
    chat_template = None
    if model_metadata.get("chat_template"):
        chat_template = ChatTemplateInfo(**model_metadata["chat_template"])

    inference_engine = None
    if model_metadata.get("inference_engine"):
        inference_engine = InferenceEngineInfo(**model_metadata["inference_engine"])

    # Determine token counting availability
    token_counting_enabled = is_token_counting_available(model_metadata)

    # Extract chat capabilities from metadata
    has_chat_template = model_metadata.get("has_chat_template", False)

    return ModelInfo(
        id=model_metadata["id"],
        object=model_metadata["object"],
        created=model_metadata.get("created"),
        owned_by=model_metadata["owned_by"],
        permission=model_metadata.get("permission", []),
        name=model_metadata["name"],
        format=model_metadata["format"],
        enabled=model_metadata["enabled"],
        training_context_length=model_metadata.get("training_context_length"),
        estimated_vram_mb=model_metadata.get("estimated_vram_mb"),
        specialties=model_metadata.get("specialties"),
        quantization=model_metadata.get("quantization"),
        parameters=model_metadata.get("parameters"),
        has_chat_template=has_chat_template,
        model_type=model_metadata.get("model_type"),
        chat_template=chat_template,
        inference_engine=inference_engine,
        supported_parameters=model_metadata.get("supported_parameters", []),
        loader_type=model_metadata.get("loader_type"),
        path=model_metadata.get("path"),
        token_counting_enabled=token_counting_enabled,
        # New fields required by Stargate
        input_schema=model_metadata.get("input_schema", "prompt"),
        parameter_defaults=model_metadata.get("parameter_defaults", {}),
        middleware_config=model_metadata.get("middleware_config", {}),
        ram_usage=model_metadata.get("ram_usage", 0),
        vram_usage=model_metadata.get("vram_usage", 0),
        context_length=model_metadata.get("context_length"),
    )
