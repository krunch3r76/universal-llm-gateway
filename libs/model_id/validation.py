"""
Model ID validation utilities.

Provides strict validation for model ID format before parsing.
"""

from __future__ import annotations


def validate_model_id(model_id: str) -> str | None:
    """
    Validate model ID format and structure.

    Rules:
    - Cannot be empty
    - Cannot have duplicate suffixes (-cpu-cpu, -hybrid-hybrid)
    - Cannot have conflicting suffixes (-cpu-hybrid)
    - Suffixes must be at end in correct order
    - Instance suffix `:N` is not supported

    Args:
        model_id: Model ID to validate

    Returns:
        Error message if invalid, None if valid
    """
    if not model_id or not model_id.strip():
        return "Model ID cannot be empty"

    # Check for duplicate suffixes
    if model_id.count("-cpu") > 1:
        return "Model ID contains duplicate -cpu suffix"
    if model_id.count("-hybrid") > 1:
        return "Model ID contains duplicate -hybrid suffix"
    if model_id.count(":") > 1:
        return "Model ID contains multiple instance suffixes"

    # Check for conflicting suffixes
    if "-cpu" in model_id and "-hybrid" in model_id:
        return "Model ID cannot have both -cpu and -hybrid suffixes"

    # Check for instance suffix (not supported)
    if ":" in model_id:
        parts = model_id.rsplit(":", 1)
        instance_str = parts[1]
        if instance_str.isdigit():
            return (
                "Model ID must not include an instance suffix like ':1' or ':2'. "
                "Remove the ':N' suffix."
            )
        return f"Model ID contains unsupported ':' segment: '{instance_str}'"

    # Check -cpu is at end
    work_id = model_id
    if "-cpu" in work_id and not work_id.endswith("-cpu"):
        return "Invalid -cpu suffix: must be at end of model ID"

    # Check -hybrid is before -cpu (or at end if no -cpu)
    if "-hybrid" in work_id:
        if work_id.endswith("-cpu"):
            check_id = work_id[:-4]
        else:
            check_id = work_id
        if not check_id.endswith("-hybrid"):
            return "Invalid -hybrid suffix: must be at end (or before -cpu)"

    return None


def validate_model_id_strict(model_id: str) -> None:
    """
    Strictly validate model ID, raising ValueError on failure.

    Use this for config loading where invalid IDs should fail fast.

    Args:
        model_id: Model ID to validate

    Raises:
        ValueError: If model ID is invalid
    """
    error = validate_model_id(model_id)
    if error:
        raise ValueError(f"Invalid model ID '{model_id}': {error}")


def validate_pipeline_model_ref(
    model_id: str, available_models: set[str]
) -> str | None:
    """
    Validate a model reference in pipeline configuration.

    Checks:
    1. Model ID format is valid
    2. Model exists in available models (with normalization)
    3. Warns about -hybrid suffix (informational, gets stripped)

    Args:
        model_id: Model ID from pipeline config
        available_models: Set of available synthetic model IDs

    Returns:
        Error message if invalid, None if valid
    """
    # First validate format
    format_error = validate_model_id(model_id)
    if format_error:
        return format_error

    # Check if model exists (normalize for comparison)
    from .model_id import ModelId

    parsed = ModelId.parse(model_id)

    # Check direct match
    if model_id in available_models:
        return None

    # Check normalized match (without -hybrid)
    if parsed.normalized in available_models:
        return None

    # Check if base synthetic exists (different context)
    base_pattern = f"{parsed.base_id}-"
    matching = [m for m in available_models if m.startswith(base_pattern)]
    if matching:
        return (
            f"Model '{model_id}' not found. "
            f"Available variants: {', '.join(sorted(matching)[:3])}"
        )

    return f"Model '{model_id}' not found in available models"
