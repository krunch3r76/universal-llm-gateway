"""
HuggingFace, AWQ, and GPTQ metadata extraction.
"""

import json
from pathlib import Path
from typing import Any

from universal_logging import get_logger

from .base import CatalogMetadata

logger = get_logger(__name__)


def extract_hf(path: Path, format_type: str = "hf") -> CatalogMetadata:
    """
    Extract metadata from HuggingFace model directory.

    Args:
        path: Path to model directory
        format_type: Format type (hf, awq, gptq)

    Returns:
        Extracted metadata
    """
    config: dict[str, Any] = {}
    tokenizer_config: dict[str, Any] = {}

    # Load config.json
    config_path = path / "config.json"
    if config_path.exists():
        try:
            with open(config_path) as f:
                config = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to read config.json: {e}")

    # Load tokenizer_config.json
    tokenizer_config_path = path / "tokenizer_config.json"
    if tokenizer_config_path.exists():
        try:
            with open(tokenizer_config_path) as f:
                tokenizer_config = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    # Detect quantization from quant_config.json
    quant = None
    quant_config: dict[str, Any] = {}

    if format_type == "awq":
        quant_config_path = path / "quant_config.json"
        if quant_config_path.exists():
            try:
                with open(quant_config_path) as f:
                    quant_config = json.load(f)
                w_bit = quant_config.get("w_bit", 4)
                quant = f"AWQ-{w_bit}bit"
            except (json.JSONDecodeError, OSError):
                pass  # Fall through to config.json fallback
        if not quant:
            # Fallback: Check config.json quantization_config
            qc = config.get("quantization_config", {})
            bits = qc.get("bits", 4)
            quant = f"AWQ-{bits}bit"

    elif format_type == "gptq":
        for cfg_name in ["quantize_config.json", "quant_config.json"]:
            cfg_path = path / cfg_name
            if cfg_path.exists():
                try:
                    with open(cfg_path) as f:
                        quant_config = json.load(f)
                    bits = quant_config.get("bits", 4)
                    quant = f"GPTQ-{bits}bit"
                    break
                except (json.JSONDecodeError, OSError):
                    pass
        if not quant:
            # Check config.json quantization_config
            qc = config.get("quantization_config", {})
            bits = qc.get("bits", 4)
            quant = f"GPTQ-{bits}bit"

    # Extract architecture from model_type
    model_type = config.get("model_type")
    arch = model_type.lower() if model_type else None

    # Extract model parameters
    hidden_size = config.get("hidden_size", 0)
    num_layers = config.get("num_hidden_layers", 0)
    intermediate_size = config.get("intermediate_size", 0)
    vocab_size = config.get("vocab_size", 0)

    parameters_m = calculate_parameters(
        hidden_size, num_layers, intermediate_size, vocab_size
    )

    # Context length
    max_position = config.get("max_position_embeddings")
    if not max_position:
        rope_scaling = config.get("rope_scaling", {})
        max_position = rope_scaling.get("original_max_position_embeddings")

    # Chat template
    has_chat_template = bool(tokenizer_config.get("chat_template"))
    input_schema = "messages" if has_chat_template else "prompt"

    capabilities: dict[str, Any] = {
        "input_schema": input_schema,
        "modalities": {"input": ["text"], "output": ["text"]},
        "interaction": {"chat_template": has_chat_template},
        "reasoning": {"supports_thinking": False},
        "limits": {},
        "provenance": {},
    }
    if max_position:
        capabilities["limits"]["max_context_length"] = max_position

    return CatalogMetadata(
        name=path.name,
        format=format_type,
        family=None,  # Can be inferred from arch if needed
        arch=arch,
        quant=quant,
        parameters_m=parameters_m,
        capabilities=capabilities,
        extra={
            "config": config,
            "tokenizer_config": tokenizer_config,
            "quant_config": quant_config,
        },
    )


def calculate_parameters(
    hidden_size: int,
    num_layers: int,
    intermediate_size: int,
    vocab_size: int,
) -> int | None:
    """Calculate parameter count from HF config."""
    if not hidden_size or not num_layers:
        return None

    # Embedding: vocab_size * hidden_size
    embed_params = vocab_size * hidden_size if vocab_size else 0

    # Attention: 4 * hidden_size^2 per layer (Q, K, V, O)
    attn_params = 4 * num_layers * hidden_size * hidden_size

    # FFN: 2 * hidden_size * intermediate_size per layer (typically)
    ffn_params = 0
    if intermediate_size:
        ffn_params = 2 * num_layers * hidden_size * intermediate_size

    # Layer norms: 2 * hidden_size per layer
    norm_params = 2 * num_layers * hidden_size

    total = embed_params + attn_params + ffn_params + norm_params

    return total // 1_000_000 if total > 0 else None
