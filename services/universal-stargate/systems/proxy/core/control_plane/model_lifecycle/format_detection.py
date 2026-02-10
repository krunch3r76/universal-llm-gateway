"""
Model format detection utilities.

Determines which inference engine to use based on model metadata.

Format Detection:
    - Requires explicit 'format' field in catalog metadata
    - No fallback behavior (fails fast on missing/unknown format)
    - Path-based detection is secondary validation only
"""

from enum import StrEnum
from typing import Any

from universal_logging import get_logger

from .replication import ModelFormat, ReplicationPolicy, get_replication_policy

logger = get_logger(__name__)


class InferenceEngine(StrEnum):
    """Supported inference engines."""

    LLAMA_CPP = "llama_cpp"  # GGUF models
    VLLM = "vllm"  # HF, AWQ, GPTQ models
    EXLLAMAV3 = "exllamav3"  # EXL3 models
    WHISPER = "whisper"  # Whisper models
    FLUX2 = "flux2"  # Flux.2 image generation models (diffusers)


# Format to engine mapping
_FORMAT_TO_ENGINE: dict[ModelFormat, str] = {
    ModelFormat.GGUF: InferenceEngine.LLAMA_CPP,
    ModelFormat.VLLM: InferenceEngine.VLLM,
    ModelFormat.HF: InferenceEngine.VLLM,
    ModelFormat.AWQ: InferenceEngine.VLLM,
    ModelFormat.GPTQ: InferenceEngine.VLLM,
    ModelFormat.EXL3: InferenceEngine.EXLLAMAV3,
    ModelFormat.WHISPER: InferenceEngine.WHISPER,
    ModelFormat.FLUX2: InferenceEngine.FLUX2,
}


class FormatDetectionError(ValueError):
    """Raised when model format cannot be determined."""


def detect_format_from_metadata(model_info: dict[str, Any]) -> ModelFormat:
    """
    Detect model format from catalog metadata.

    Args:
        model_info: Model metadata dict

    Returns:
        Detected ModelFormat

    Raises:
        FormatDetectionError: If format is missing or unknown (no fallback)
    """
    format_str = model_info.get("format", "").lower()

    if not format_str:
        model_id = model_info.get("name", model_info.get("model_id", "unknown"))
        raise FormatDetectionError(
            f"Model '{model_id}' missing required 'format' field in catalog metadata"
        )

    try:
        return ModelFormat(format_str)
    except ValueError:
        model_id = model_info.get("name", model_info.get("model_id", "unknown"))
        valid_formats = ", ".join(f.value for f in ModelFormat)
        raise FormatDetectionError(
            f"Model '{model_id}' has unknown format '{format_str}'. "
            f"Valid formats: {valid_formats}"
        )


def detect_format_from_path(path: str) -> ModelFormat:
    """
    Detect model format from file path/extension.

    Used only for validation hints, not as primary format source.
    Catalog metadata 'format' field is the authoritative source.

    Args:
        path: Model file path

    Returns:
        Detected ModelFormat

    Raises:
        FormatDetectionError: If format cannot be determined from path
    """
    path_lower = path.lower()

    if path_lower.endswith(".gguf"):
        return ModelFormat.GGUF
    elif path_lower.endswith(".safetensors"):
        # SafeTensors could be HF, AWQ, or GPTQ
        if "awq" in path_lower:
            return ModelFormat.AWQ
        elif "gptq" in path_lower:
            return ModelFormat.GPTQ
        else:
            return ModelFormat.HF
    elif path_lower.endswith(".pt") or path_lower.endswith(".bin"):
        if "whisper" in path_lower:
            return ModelFormat.WHISPER
        # No flux heuristics - require explicit format: flux2 in catalog
        return ModelFormat.HF
    elif "exl3" in path_lower or "exllama" in path_lower:
        return ModelFormat.EXL3

    raise FormatDetectionError(
        f"Cannot determine format from path '{path}'. "
        "Set explicit 'format' field in model catalog metadata."
    )


def get_engine_for_model(model_info: dict[str, Any]) -> InferenceEngine:
    """
    Get inference engine for a model.

    Args:
        model_info: Model metadata dict

    Returns:
        InferenceEngine to use

    Raises:
        FormatDetectionError: If format is missing/unknown
        ValueError: If format has no mapped engine (should not happen)
    """
    model_format = detect_format_from_metadata(model_info)
    engine = _FORMAT_TO_ENGINE.get(model_format)
    if engine is None:
        # This should never happen if _FORMAT_TO_ENGINE is complete
        raise ValueError(
            f"No engine mapping for format '{model_format.value}'. "
            "This is a configuration error."
        )
    return InferenceEngine(engine)


def get_replication_policy_for_model(model_info: dict[str, Any]) -> ReplicationPolicy:
    """
    Get replication policy for a model.

    Args:
        model_info: Model metadata dict

    Returns:
        ReplicationPolicy based on model format
    """
    model_format = detect_format_from_metadata(model_info)
    return get_replication_policy(model_format.value)


def supports_multi_instance_per_gateway(model_info: dict[str, Any]) -> bool:
    """
    Check if model supports multiple instances per gateway.

    Args:
        model_info: Model metadata dict

    Returns:
        True if model format allows multiple instances per gateway
    """
    policy = get_replication_policy_for_model(model_info)
    return policy.max_instances_per_gateway == 0 or policy.max_instances_per_gateway > 1


def engine_supports_batching(engine: InferenceEngine) -> bool:
    """
    Check if engine supports native request batching.

    Args:
        engine: Inference engine

    Returns:
        True if engine has native batching (vLLM, ExLlamaV3)
    """
    batching_engines = {InferenceEngine.VLLM, InferenceEngine.EXLLAMAV3}
    return engine in batching_engines
