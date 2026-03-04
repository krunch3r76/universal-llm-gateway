"""
EXL3 (ExLlamaV3) metadata extraction.
"""

import json
from pathlib import Path
from typing import Any

from universal_logging import get_logger

from .base import CatalogMetadata
from .hf import calculate_parameters

logger = get_logger(__name__)


def extract_exl3(path: Path) -> CatalogMetadata:
    """
    Extract metadata from EXL3 model directory.

    EXL3 models are ExLlamaV3 quantized models with .exl3 weight files.

    Args:
        path: Path to EXL3 model directory

    Returns:
        Extracted metadata
    """
    config: dict[str, Any] = {}
    tokenizer_config: dict[str, Any] = {}
    quant_config: dict[str, Any] = {}

    # Load config.json (HF-compatible config)
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

    # Load quant_config.json (EXL3 quantization config)
    quant_config_path = path / "quant_config.json"
    if quant_config_path.exists():
        try:
            with open(quant_config_path) as f:
                quant_config = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    # Extract architecture from model_type
    model_type = config.get("model_type")
    arch = model_type.lower() if model_type else None

    # Determine quantization from quant_config
    quant = None
    if quant_config:
        bits = quant_config.get("bits")
        if bits:
            quant = f"EXL3-{bits}bpw"

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
        format="exl3",
        family=None,
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
