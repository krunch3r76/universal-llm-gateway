"""Model metadata definitions and base extraction logic"""

import json
from dataclasses import dataclass
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


@dataclass
class ModelMetadata:
    """Model metadata container"""

    id: str
    name: str
    format: str
    enabled: bool
    path: str
    training_context_length: int | None
    estimated_vram_mb: int
    specialties: list[str]
    quantization: str
    parameters: str
    loader_config: dict[str, Any]
    chat_template: str | None = None
    chat_template_supports_system: bool = False


class MetadataExtractor:
    """Metadata extraction using inference_djinn inspectors directly"""

    def __init__(self):
        # Import complete inspector functions from inference_djinn
        from inference_djinn.engines.gguf.inspector import (
            get_model_info_summary as gguf_inspect,
        )
        from inference_djinn.engines.vllm.inspector import (
            get_vllm_model_info as vllm_inspect,
        )

        self.djinn_inspector_lookup = {
            "gguf": gguf_inspect,
            "awq": vllm_inspect,
            "gptq": vllm_inspect,
            "hf": vllm_inspect,  # Add explicit HF support
        }

    def extract_metadata(
        self, model_id: str, model_data: dict[str, Any]
    ) -> ModelMetadata:
        """Extract metadata using inference_djinn inspectors directly, with extensive debug logging"""
        model_path = model_data.get("path", "")
        model_format = model_data.get("format", "unknown")
        logger.debug(
            f"Starting metadata extraction for model_id={model_id}, model_format={model_format}, model_path={model_path}"
        )
        logger.debug(f"Model data: {json.dumps(model_data, default=str)}")

        if model_format == "api_proxy":
            raise ValueError(
                f"API proxy format is no longer supported in the gateway for model_id={model_id}"
            )
        else:
            djinn_inspector = self.djinn_inspector_lookup.get(model_format)
            if not djinn_inspector:
                logger.warning(
                    f"No djinn inspector for format '{model_format}', using GGUF as fallback for model_id={model_id}"
                )
                djinn_inspector = self.djinn_inspector_lookup.get("gguf")
            else:
                logger.debug(
                    f"Found djinn inspector for format '{model_format}' for model_id={model_id}"
                )

            if djinn_inspector:
                try:
                    logger.debug(
                        f"Invoking djinn inspector for model_id={model_id}, model_path={model_path}"
                    )
                    djinn_summary = djinn_inspector(model_path)
                    logger.debug(
                        f"Djinn summary for model_id={model_id}: {json.dumps(djinn_summary, default=str)}"
                    )
                    context_length, chat_template, chat_template_supports_system = (
                        self._extract_metadata_from_djinn_summary(djinn_summary)
                    )
                    logger.debug(
                        f"Extracted from djinn summary for model_id={model_id}: "
                        f"context_length={context_length}, chat_template={chat_template}, chat_template_supports_system={chat_template_supports_system}"
                    )
                    logger.info(
                        f"Extracted metadata for {model_id} using djinn {model_format} inspector"
                    )
                except Exception as e:
                    logger.error(
                        f"Error using inference_djinn inspector for {model_format} (model_id={model_id}): {e}",
                        exc_info=True,
                    )
                    context_length, chat_template, chat_template_supports_system = (
                        None,
                        None,
                        False,
                    )
            else:
                logger.error(
                    f"No djinn inspector available for model_id={model_id}, model_format={model_format}"
                )
                context_length, chat_template, chat_template_supports_system = (
                    None,
                    None,
                    False,
                )

        model_metadata = ModelMetadata(
            id=model_id,
            name=model_data.get("name", model_id),
            format=model_format,
            enabled=model_data.get("enabled", True),
            path=model_path,
            training_context_length=context_length,
            estimated_vram_mb=model_data.get("estimated_vram_mb", 0),
            specialties=model_data.get("specialties", []),
            quantization=model_data.get("quantization", "unknown"),
            parameters=model_data.get("parameters", "unknown"),
            loader_config=model_data.get("loader_config", {}),
            chat_template=chat_template,
            chat_template_supports_system=chat_template_supports_system,
        )
        return model_metadata

    def _extract_metadata_from_djinn_summary(
        self, djinn_summary: dict[str, Any]
    ) -> tuple[int | None, str | None, bool]:
        """Extract metadata from complete djinn inspector summary"""

        # Extract context length from capabilities.limits or metadata
        capabilities = djinn_summary.get("capabilities", {})
        limits = capabilities.get("limits", {}) if isinstance(capabilities, dict) else {}
        context_length = limits.get("max_context_length")

        if context_length is None:
            metadata = capabilities.get("metadata", {}) if isinstance(capabilities, dict) else {}
            context_length = metadata.get("training_context_length")
        if context_length is None:
            main_metadata = djinn_summary.get("metadata", {})
            context_length = main_metadata.get("training_context_length")

        if context_length == "unknown":
            context_length = None

        # Extract chat template information
        chat_template_info = djinn_summary.get("chat_template", {})

        if chat_template_info:
            has_chat_template = chat_template_info.get("has_chat_template", False)
            template_type = chat_template_info.get("template_type", "none")

            if template_type == "transformers_tokenizer" and has_chat_template:
                # For transformers models, we indicate chat template support but don't store the template
                chat_template = "tokenizer_template"
                chat_template_supports_system = (
                    True  # Most modern models support system messages
                )
            elif template_type == "gguf_metadata" and has_chat_template:
                # Extract the actual chat template from GGUF metadata
                chat_template = chat_template_info.get("chat_template")
                chat_template_supports_system = (
                    True  # GGUF models typically support system messages
                )
            elif template_type == "wizard_vicuna_truncation" and has_chat_template:
                # Special handling for wizard-vicuna models
                chat_template = "wizard_vicuna_truncation"
                chat_template_supports_system = (
                    False  # Wizard-vicuna truncates system messages
                )
            else:
                chat_template = None
                chat_template_supports_system = False
        else:
            chat_template = None
            chat_template_supports_system = False

        return context_length, chat_template, chat_template_supports_system
