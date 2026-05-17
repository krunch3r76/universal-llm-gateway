"""
Model capability analysis for GGUF models.

Aggregates file properties, metadata summary, chat-template analysis, and
recommended config into the capability output dict.
"""

import os
from typing import Any

from .chat_template_analysis import detect_chat_template_support
from .config_recommendations import generate_recommended_config
from .gguf_metadata import GGUFMetadataLite
from .metadata_loading import load_gguf_metadata


def analyze_model_capabilities(
    model_path: str, meta: GGUFMetadataLite | None = None
) -> dict[str, Any]:
    """
    Analyze GGUF model capabilities and configuration.

    Args:
        model_path: Path to the GGUF model file
        meta: Optional GGUFMetadataLite object (will load if not provided)

    Returns:
        Dictionary with model capability analysis
    """
    if meta is None:
        meta = load_gguf_metadata(model_path)

    capabilities = {
        "model_path": model_path,
        "file_exists": os.path.exists(model_path),
        "file_size_mb": 0,
        "estimated_parameters": "unknown",
        "format": "gguf",
        "chat_template_analysis": None,
        "recommended_config": {},
    }

    if capabilities["file_exists"]:
        try:
            file_size = os.path.getsize(model_path)
            capabilities["file_size_mb"] = round(file_size / (1024 * 1024), 2)
        except Exception as e:
            capabilities["file_error"] = str(e)

    # Add metadata information if available
    if meta:
        capabilities.update(
            {
                "metadata": {
                    "name": meta.name,
                    "architecture": meta.architecture,
                    "context_length": meta.context_length,
                    "embedding_length": meta.embedding_length,
                    "block_count": meta.block_count,
                    "head_count": meta.head_count,
                    "tokenizer_model": meta.tokenizer_model,
                    "chat_template_available": bool(meta.chat_template),
                }
            }
        )

        # Estimate parameters from metadata
        if meta.block_count > 0 and meta.embedding_length > 0:
            # Rough parameter estimation
            params_estimate = (
                meta.block_count * meta.embedding_length * 4
            )  # Very rough estimate
            if params_estimate > 1e9:
                capabilities["estimated_parameters"] = f"~{params_estimate / 1e9:.1f}B"
            elif params_estimate > 1e6:
                capabilities["estimated_parameters"] = f"~{params_estimate / 1e6:.0f}M"

    # Analyze chat template support
    capabilities["chat_template_analysis"] = detect_chat_template_support(
        model_path, meta
    )

    # Generate recommended configuration
    capabilities["recommended_config"] = (
        generate_recommended_config(meta) if meta else {}
    )

    return capabilities
