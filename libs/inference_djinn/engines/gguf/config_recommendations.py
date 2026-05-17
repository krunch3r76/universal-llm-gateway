"""
Configuration recommendations for GGUF models.

Generates llama-cpp-python runtime configuration recommendations from parsed GGUF metadata.
"""

import os
from typing import Any

from .gguf_metadata import GGUFMetadataLite
from .metadata_values import _safe_numpy_to_string


def generate_recommended_config(meta: GGUFMetadataLite) -> dict[str, Any]:
    """
    Generate recommended llama-cpp-python config based on GGUF metadata.

    Args:
        meta: GGUFMetadataLite with parsed model metadata.

    Returns:
        Recommended llama-cpp config.
    """
    cpu_count = os.cpu_count() or 8

    # ─── Base Config ───
    config = {
        "n_ctx": meta.context_length,
        "n_gpu_layers": -1,  # Default to full offload (adjust based on GPU VRAM externally if needed)
        "n_batch": 512,
        "n_threads": min(cpu_count, 16),
        "verbose": False,
        "use_mlock": True,
        "use_mmap": True,
        "f16_kv": True,
    }

    # ─── Adjust n_batch Based on Model Size ───
    # Use float arithmetic to avoid overflow
    param_estimate = (
        float(meta.block_count)
        * float(meta.embedding_length)
        * float(meta.feed_forward_length)
        * float(meta.head_count)
    )
    if (
        param_estimate > 50_000_000_000
    ):  # Arbitrary high threshold (50B "parameter proxy")
        config.update(
            {
                "n_batch": 256,
                "n_threads": min(cpu_count, 16),
            }
        )
    elif param_estimate < 7_000_000_000:  # Small models, likely 7B-class or smaller
        config.update(
            {
                "n_batch": 1024,
                "n_ctx": min(
                    8192, meta.context_length * 2
                ),  # Cap ctx to double the trained context length
            }
        )

    # ─── Adjust for Chat Template ───
    chat_template = _safe_numpy_to_string(getattr(meta, "chat_template", ""), "")

    if chat_template and "vicuna" in chat_template.lower():
        config["force_chat_template"] = True

    # ─── Adjust ROPE Scaling (if applicable) ───
    if config["n_ctx"] > meta.context_length:
        config["rope_scaling_type"] = "linear"
        config["rope_freq_base"] = meta.rope_freq_base

    # ─── Platform-Specific mlock Handling ───
    if os.name == "nt":  # Windows
        config["use_mlock"] = False

    return config
