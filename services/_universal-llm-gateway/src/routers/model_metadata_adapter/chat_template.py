"""Chat template resolution from YAML configuration and djinn inspector probes.

Reads template metadata from model_loaders.yaml and optionally verifies support
via GGUF or vLLM djinn inspectors when a filesystem path is available.
"""

from typing import Any

from universal_logging import get_logger

from ...core.model_registry import ModelRegistry
from .text_normalization import safe_lower

logger = get_logger(__name__)


def has_working_chat_template(
    registry: ModelRegistry, model_path: str, model_format: str
) -> bool:
    """Check if model has a working chat template using djinn inspector results."""
    model_metadata = None
    for metadata in registry.models_to_metadata.values():
        if metadata.path == model_path:
            model_metadata = metadata
            break

    if not model_metadata:
        logger.warning(f"Model metadata not found for path: {model_path}")
        return False

    try:
        if model_format == "gguf":
            from inference_djinn.engines.gguf.inspector import (
                detect_chat_template_support,
            )

            chat_template_support = detect_chat_template_support(model_path)
            has_working_template = chat_template_support.get("has_chat_template", False)
            template_type = chat_template_support.get("template_type", "none")

            logger.debug(
                "Model %s chat template support: %s, type: %s",
                model_path,
                has_working_template,
                template_type,
            )
            return has_working_template
        if model_format in ["awq", "gptq", "hf"]:
            from inference_djinn.engines.vllm.inspector import get_vllm_model_info

            model_info = get_vllm_model_info(model_path)
            tokenizer_info = model_info.get("detailed_info", {}).get(
                "tokenizer_info", {}
            )
            has_working_template = tokenizer_info.get("has_chat_template", False)

            logger.debug(
                f"Model {model_path} chat template support (vLLM): "
                f"{has_working_template}"
            )
            return has_working_template
        return False

    except Exception as exc:
        logger.error(f"Error checking chat template support for {model_path}: {exc}")
        return False


def get_chat_template_from_config(
    model_id: str, model_config: dict[str, Any]
) -> dict[str, Any]:
    """Get chat template information from YAML configuration."""
    if not isinstance(model_config, dict):
        return {
            "exists": False,
            "content": None,
            "supports_system_role": False,
            "source": None,
        }

    model_info = model_config.get("metadata") or model_config.get("info") or {}
    capabilities = model_info.get("capabilities", {})
    interaction = capabilities.get("interaction", {})
    has_chat = interaction.get("chat_template", False)
    input_schema = (
        capabilities.get("input_schema") or model_info.get("input_schema") or "prompt"
    )

    logger.debug(
        "📄 get_chat_template_from_config(%s): reading from YAML config - "
        "family=%s, input_schema=%s, chat_template=%s",
        model_id,
        model_info.get("family"),
        input_schema,
        has_chat,
    )

    if "chat_template" in model_info:
        return {
            "exists": True,
            "content": model_info["chat_template"],
            "supports_system_role": model_info.get("supports_system_role", True),
            "source": "yaml_config",
        }

    if has_chat:
        model_family = safe_lower(model_info.get("family")) or "default"
        return {
            "exists": True,
            "content": f"{model_family}_chat_template",
            "supports_system_role": True,
            "source": "capabilities",
        }

    model_family = safe_lower(model_info.get("family"))

    if input_schema == "messages":
        template_type = model_family if model_family else "default"
        return {
            "exists": True,
            "content": f"{template_type}_chat_template",
            "supports_system_role": True,
            "source": "inferred_from_family",
        }

    if model_family in ["llama", "mistral", "qwen", "deepseek"]:
        return {
            "exists": True,
            "content": f"{model_family}_chat_template",
            "supports_system_role": False,
            "source": "inferred_from_family",
        }

    return {
        "exists": False,
        "content": None,
        "supports_system_role": False,
        "source": None,
    }
