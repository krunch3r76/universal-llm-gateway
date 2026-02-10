"""Config-based resource queries and estimation.

Provides resource requirement lookups from YAML configuration and
heuristic-based estimation for model properties.
"""

from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


def get_model_resources_from_config(model_id: str) -> tuple[int | None, int | None]:
    """
    Get resource requirements for a model from YAML configuration.

    Args:
        model_id: Model ID to get resources for

    Returns:
        Tuple of (vram_mb, ram_mb) from YAML config, or (None, None) if not found
    """
    try:
        import sys

        from src.core.model_registry import ModelRegistry

        if hasattr(sys.modules.get("src.core.model_registry"), "model_registry"):
            registry = sys.modules["src.core.model_registry"].model_registry
        else:
            from src.core.config_loader import config_loader

            config = config_loader.load_model_loaders_config()
            registry = ModelRegistry(config)

        resources = registry.get_model_resources(model_id)
        if resources:
            return resources.get("vram_mb"), resources.get("ram_mb")
        return None, None

    except Exception as e:
        logger.warning(f"Failed to get model resources for {model_id}: {e}")
        return None, None


def detect_quantization(model_id: str) -> str | None:
    """Detect quantization type from model ID."""
    model_id_lower = model_id.lower()
    if "awq" in model_id_lower:
        return "awq"
    elif "gptq" in model_id_lower:
        return "gptq"
    elif "gguf" in model_id_lower:
        return "gguf"
    return None


def estimate_context_length(model_id: str) -> int | None:
    """
    Estimate context length from model ID.

    Uses heuristics based on model size indicators in the ID.
    """
    model_lower = model_id.lower()
    if "7b" in model_lower:
        return 4096
    elif "13b" in model_lower:
        return 8192
    elif "33b" in model_lower:
        return 16384
    elif "70b" in model_lower:
        return 32768
    return 8192  # Default


def get_model_requirements(model_id: str) -> dict[str, Any]:
    """
    Get resource requirements for a model from YAML configuration.

    Returns:
        Dict containing resource requirements and estimates
    """
    from src.core.model_registry.registry import normalize_model_id

    normalized_id = normalize_model_id(model_id)
    config_vram, config_ram = get_model_resources_from_config(normalized_id)

    # Estimate load/unload times based on model size
    if config_vram is not None:
        estimated_load_time = max(10.0, config_vram / 1000)
        estimated_unload_time = max(2.0, config_vram / 2000)
    else:
        estimated_load_time = 10.0
        estimated_unload_time = 2.0

    return {
        "model_id": model_id,
        "vram_required_mb": config_vram,
        "ram_required_mb": config_ram,
        "estimated_load_time": estimated_load_time,
        "estimated_unload_time": estimated_unload_time,
        "quantization": detect_quantization(model_id),
        "model_size_mb": config_vram,
        "context_length": estimate_context_length(model_id),
    }
