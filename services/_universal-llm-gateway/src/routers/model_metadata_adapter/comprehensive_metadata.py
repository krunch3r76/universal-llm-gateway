"""Build comprehensive per-model metadata payloads for model_info API endpoints.

Assembles chat template, inference engine, resource usage, middleware config,
and supported parameters into the unified metadata dict returned to Stargate.
"""

import time
from typing import Any

from universal_logging import get_logger

from ...core.model_registry import ModelRegistry
from .chat_template import get_chat_template_from_config
from .inference_engine import get_inference_engine_info

logger = get_logger(__name__)


def get_comprehensive_model_metadata(
    registry: ModelRegistry,
    model_id: str,
    inference_engine_specs: dict[str, Any],
    get_model_type_from_config,
) -> dict[str, Any] | None:
    """Get comprehensive metadata for a single model."""
    model_info = registry.get_model_info(model_id)
    if not model_info:
        return None

    model_config_key = registry.find_config_key_for_openai_id(model_id)
    if not model_config_key:
        return None

    models_data = registry.model_loaders_config.get("models", {})
    model_config = models_data.get(model_config_key, {})
    model_info_section = model_config.get("metadata") or model_config.get("info") or {}

    logger.debug(
        f"🔍 get_comprehensive_model_metadata({model_id}): "
        f"config_key={model_config_key}, has_config={bool(model_config)}, "
        f"has_info={bool(model_info_section)}"
    )

    model_path = model_info_section.get("path", "")
    chat_template_info = get_chat_template_from_config(model_id, model_config)
    inference_engine_info = get_inference_engine_info(
        registry, model_id, inference_engine_specs
    )
    model_type = get_model_type_from_config(model_id, model_config)

    parameters = model_info_section.get("parameters")
    if parameters is not None and not isinstance(parameters, str):
        parameters = str(parameters)

    ram_usage = 0
    vram_usage = 0
    context_length = None

    profiles = model_config.get("profiles", {})
    if profiles:
        profile_keys = [key for key in profiles.keys() if key.isdigit()]
        if profile_keys:
            highest_context_key = max(profile_keys, key=int)
            highest_profile = profiles[highest_context_key]
            resources = highest_profile.get("resources", {})
            ram_usage = resources.get("ram_mb", 0)
            vram_usage = resources.get("vram_mb", 0)
            context_length = int(highest_context_key)

    base_loader = model_config.get("base_loader", {})
    parameter_defaults = {
        "temperature": 0.7,
        "top_p": 0.9,
        "max_tokens": 2048,
        **base_loader,
    }

    caps_section = model_info_section.get("capabilities", {})
    input_schema = caps_section.get("input_schema") or model_info_section.get(
        "input_schema", "prompt"
    )
    model_family = model_info_section.get("family", "default")
    preserve_personality = model_family != "default" and input_schema != "messages"

    middleware_config = {
        "preserve_personality": preserve_personality,
        "supports_system_role": chat_template_info.get("supports_system_role", False)
        if chat_template_info
        else False,
        "model_family": model_family,
        "sticky": model_info_section.get("sticky", True),
    }

    supported_parameters = ["temperature", "top_p", "max_tokens", "stop"]
    if model_info.format in ["gguf"]:
        supported_parameters.extend(
            [
                "repeat_penalty",
                "tfs_z",
                "typical_p",
                "presence_penalty",
                "frequency_penalty",
            ]
        )

    return {
        "id": model_id,
        "object": "model",
        "created": int(time.time()),
        "owned_by": "universal-llm-gateway",
        "name": model_info.name,
        "format": model_info.format,
        "enabled": model_info.enabled,
        "training_context_length": model_info.training_context_length,
        "estimated_vram_mb": model_info.estimated_vram_mb,
        "specialties": model_info_section.get("specialties"),
        "quantization": model_info_section.get("quantization"),
        "parameters": parameters,
        "model_type": model_type,
        "chat_template": chat_template_info,
        "inference_engine": inference_engine_info,
        "loader_type": model_info_section.get("loader_type", "unknown"),
        "path": model_path,
        "has_chat_template": chat_template_info.get("exists", False)
        if chat_template_info
        else False,
        "input_schema": input_schema,
        "parameter_defaults": parameter_defaults,
        "middleware_config": middleware_config,
        "ram_usage": ram_usage,
        "vram_usage": vram_usage,
        "context_length": context_length,
        "supported_parameters": supported_parameters,
    }
