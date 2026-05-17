"""
Model requirements validation for GGUF engine.

Validates GGUF model path, extension, metadata sanity, file size, and
llama-cpp runtime availability.
"""

import os
from typing import Any

from .gguf_metadata import GGUFMetadataLite
from .metadata_loading import load_gguf_metadata


def validate_model_requirements(
    model_path: str, meta: GGUFMetadataLite | None = None
) -> dict[str, Any]:
    """
    Validate that model meets requirements for GGUF engine.

    Args:
        model_path: Path to the model file
        meta: Optional GGUFMetadataLite object (will load if not provided)

    Returns:
        Validation results dictionary
    """
    if meta is None:
        meta = load_gguf_metadata(model_path)

    validation = {"valid": True, "errors": [], "warnings": [], "requirements_met": True}

    # Check file exists
    if not os.path.exists(model_path):
        validation["errors"].append(f"Model file not found: {model_path}")
        validation["valid"] = False
        return validation

    # Check file extension
    if not model_path.lower().endswith((".gguf", ".ggml")):
        validation["warnings"].append(
            f"File extension suggests non-GGUF format: {model_path}"
        )

    # Check metadata loading
    if meta is None:
        validation["warnings"].append(
            "Could not load GGUF metadata - may indicate format issues"
        )
    else:
        # Validate metadata content
        if meta.architecture == "unknown":
            validation["warnings"].append("Model architecture not recognized")
        if meta.context_length <= 0:
            validation["warnings"].append("Context length not specified or invalid")
        if meta.block_count <= 0:
            validation["warnings"].append("Block count not specified or invalid")

    # Check file size (minimum reasonable size)
    try:
        file_size = os.path.getsize(model_path)
        if file_size < 10 * 1024 * 1024:  # < 10MB
            validation["warnings"].append(
                "File size is very small - may not be a complete model"
            )
        elif file_size > 100 * 1024 * 1024 * 1024:  # > 100GB
            validation["warnings"].append(
                "File size is very large - may require special handling"
            )
    except Exception as e:
        validation["errors"].append(f"Could not check file size: {e}")

    # Check dependencies
    try:
        import llama_cpp  # noqa: F401
    except ImportError:
        validation["errors"].append(
            "llama-cpp-python not available - required for GGUF engine"
        )
        validation["valid"] = False

    if validation["errors"]:
        validation["valid"] = False
        validation["requirements_met"] = False

    return validation
