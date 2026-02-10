"""
Pure utility functions for ModelMetadata operations.
"""

from typing import Any

from gateway_client import ModelMetadata


def metadata_to_monitoring_dict(
    metadata: ModelMetadata, model_id: str
) -> dict[str, Any]:
    """
    Convert ModelMetadata to monitoring-friendly dict format.

    This is a pure function optimized for monitoring/logging use cases.
    """
    return {
        "format": metadata.format,
        "input_schema": metadata.input_schema,
        "context_length": metadata.context_length,
        "model_id": model_id,
        "path": metadata.path,
        "loader_type": metadata.loader_type,
    }


def metadata_to_profile_dict(metadata: ModelMetadata | None) -> dict[str, Any]:
    """
    Convert ModelMetadata to profile-manager-friendly dict format.

    Returns minimal format info needed for profile compatibility checks.
    """
    if not metadata:
        return {}

    return {
        "format": metadata.format,
        "input_schema": metadata.input_schema,
        "loader_type": metadata.loader_type,
    }


def extract_input_schema(metadata: ModelMetadata | None) -> str:
    """
    Extract input_schema from ModelMetadata with safe fallback.

    Returns 'prompt' as default if metadata is None or missing input_schema.
    """
    if not metadata:
        return "prompt"
    return metadata.input_schema or "prompt"


def should_transform_to_prompt(metadata: ModelMetadata | None) -> bool:
    """
    Determine if messages should be transformed to prompt format.

    Pure predicate function for transformation decisions.
    """
    input_schema = extract_input_schema(metadata)
    return input_schema != "messages"


def is_cpu_model(metadata: ModelMetadata | None) -> bool:
    """
    Determine if model is CPU-only based on loader type.

    Pure predicate for routing decisions.
    """
    if not metadata:
        return True  # Default to CPU if unknown

    return metadata.loader_type in [
        "llama_cpp_cpu",
        "cpu",
    ] or metadata.loader_type.endswith("-cpu")


def is_gpu_model(metadata: ModelMetadata | None) -> bool:
    """
    Determine if model uses GPU based on loader type.

    Pure predicate for routing decisions.
    """
    return not is_cpu_model(metadata)


def get_resource_requirements(metadata: ModelMetadata | None) -> dict[str, int]:
    """
    Extract resource requirements from ModelMetadata.

    Returns dict with 'ram_mb' and 'vram_mb' keys.
    """
    if not metadata:
        return {"ram_mb": 0, "vram_mb": 0}

    return {"ram_mb": metadata.ram_usage, "vram_mb": metadata.vram_usage}


def get_context_length(metadata: ModelMetadata | None) -> int | None:
    """
    Extract context length with safe fallback.

    Returns None if not available.
    """
    if not metadata:
        return None
    return metadata.context_length


def is_model_enabled(metadata: ModelMetadata | None) -> bool:
    """
    Check if model is enabled.

    Pure predicate for availability checks.
    """
    if not metadata:
        return False
    return metadata.enabled
