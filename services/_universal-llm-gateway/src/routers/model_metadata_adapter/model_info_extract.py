"""Pure mapping from model_loaders.yaml configuration to API response fields.

Extracts OpenAI API fields, capabilities, loader config, and resource usage
into the comprehensive model info dict consumed by multiple router endpoints.
"""

from typing import Any


def extract_comprehensive_model_info(
    model_id: str, model_config: dict[str, Any]
) -> dict[str, Any]:
    """
    Extract comprehensive model information from configuration.

    This is a pure function that maps model configuration to API response format.
    Used by multiple router endpoints to ensure consistent field mapping.

    Args:
        model_id: The model identifier
        model_config: Raw model configuration from model_loaders.yaml

    Returns:
        Dictionary with all model fields mapped to API response format

    Raises:
        ValueError: If required sections are missing
    """
    model_info = model_config.get("info", {})
    if not model_info:
        raise ValueError(f"Model '{model_id}' missing required 'info' section")

    openai_fields = model_info.get("openai_api_fields", {})
    if not openai_fields:
        raise ValueError(f"Model '{model_id}' missing required 'openai_api_fields'")

    loader_config = model_config.get("base_loader", {}).copy()

    profiles = model_config.get("profiles", {})
    profile_keys = [key for key in profiles.keys() if key.isdigit()]
    if profile_keys:
        selected_key = max(profile_keys, key=int)
        profile_config = profiles[selected_key]
        if profile_config.get("loader"):
            loader_config.update(profile_config["loader"])

    capabilities = model_info.get("capabilities", {})
    limits = capabilities.get("limits", {})
    provenance = capabilities.get("provenance", {})
    input_schema = capabilities.get("input_schema", "messages")

    return {
        "id": openai_fields.get("id", model_id),
        "object": openai_fields.get("object", "model"),
        "owned_by": openai_fields.get("owned_by", "universal-llm-gateway"),
        "permission": openai_fields.get("permission", ["generate"]),
        "name": model_info.get("name"),
        "format": model_info.get("format"),
        "enabled": model_info.get("enabled"),
        "path": model_info.get("path"),
        "ram_usage": model_info.get("ram_usage"),
        "vram_usage": model_info.get("vram_usage"),
        "training_context_length": limits.get("max_context_length"),
        "input_schema": input_schema,
        "training_cutoff_year": model_info.get("training_cutoff_year"),
        "model_family": model_info.get("family"),
        "quantization": model_info.get("quant"),
        "architecture": model_info.get("arch"),
        "license": provenance.get("license"),
        "parameters": model_info.get("parameters"),
        "release_date": model_info.get("release_date"),
        "description": model_info.get("description"),
        "capabilities": capabilities,
        "loader_config": loader_config,
        "loader": model_config.get("loader", {}),
        "resources": model_config.get("resources", {}),
    }
