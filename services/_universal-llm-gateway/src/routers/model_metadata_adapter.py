"""
Model Metadata Adapter

Provides API-friendly formatting for model metadata extracted by the ModelRegistry.
This adapter reshapes registry data and adds API-specific logic for HTTP responses.
"""

import time
from typing import Any

from universal_logging import get_logger

from ..core.model_registry import ModelRegistry

logger = get_logger(__name__)


def safe_lower(value: str | None) -> str:
    """Safely convert a value to lowercase string, handling None."""
    return (value or "").lower()


def extract_comprehensive_model_info(
    model_id: str, model_config: dict[str, Any]
) -> dict[str, Any]:
    """
    Extract comprehensive model information from configuration.

    This is a pure function that maps model configuration to API response format.
    Used by multiple router endpoints to ensure consistent field mapping.

    Args:
        model_id: The model identifier
        model_config: Raw model configuration from model_loaders.yaml

    Returns:
        Dictionary with all model fields mapped to API response format

    Raises:
        ValueError: If required sections are missing
    """
    # All models now use standardized 'info' section structure
    model_info = model_config.get("info", {})
    if not model_info:
        raise ValueError(f"Model '{model_id}' missing required 'info' section")

    openai_fields = model_info.get("openai_api_fields", {})
    if not openai_fields:
        raise ValueError(f"Model '{model_id}' missing required 'openai_api_fields'")

    # Compile loader config (base + default profile) - same as used internally
    # Note: profile selection is internal to synthetic model generation
    loader_config = model_config.get("base_loader", {}).copy()

    # Find highest context profile for resource extraction (internal use only)
    profiles = model_config.get("profiles", {})
    profile_keys = [key for key in profiles.keys() if key.isdigit()]
    if profile_keys:
        selected_key = max(profile_keys, key=int)
        profile_config = profiles[selected_key]
        if profile_config.get("loader"):
            loader_config.update(profile_config["loader"])

    # Create comprehensive model info with ALL fields
    return {
        # OpenAI API fields
        "id": openai_fields.get("id", model_id),
        "object": openai_fields.get("object", "model"),
        "owned_by": openai_fields.get("owned_by", "universal-llm-gateway"),
        "permission": openai_fields.get("permission", ["generate"]),
        # Basic model fields (all fields from model_loaders.yaml)
        "name": model_info.get("name"),
        "format": model_info.get("format"),
        "enabled": model_info.get("enabled"),
        "path": model_info.get("path"),
        # Resource usage
        "ram_usage": model_info.get("ram_usage"),
        "vram_usage": model_info.get("vram_usage"),
        # Standardized metadata
        "training_context_length": model_info.get("training_context_length"),
        "supports_chat_history": model_info.get("supports_chat_history"),
        "input_schema": model_info.get("input_schema"),
        "training_cutoff_year": model_info.get("training_cutoff_year"),
        "model_family": model_info.get("family"),
        "quantization": model_info.get("quant"),
        "architecture": model_info.get("arch"),
        "license": model_info.get("license"),
        "parameters": model_info.get("parameters"),
        "release_date": model_info.get("release_date"),
        "description": model_info.get("description"),
        "capabilities": model_info.get("capabilities"),
        "safety_info": model_info.get("safety_info"),
        # Loader configuration - the actual config used by workers
        "loader_config": loader_config,
        # Legacy fields for simple loader format
        "loader": model_config.get("loader", {}),
        "resources": model_config.get("resources", {}),
    }


class ModelMetadataAdapter:
    """
    Adapter for providing API-friendly model metadata formatting.

    This adapter takes metadata already extracted by the ModelRegistry and:
    - Reshapes it for API/HTTP consumption
    - Adds API-specific logic (overrides, inference engine specs)
    - Provides unified interface for model metadata queries
    - Serves as formatting layer between registry and API endpoints

    Note: This adapter does NOT analyze model files directly - that's done by the
    ModelRegistry.

    API Endpoints that use this adapter:
    - GET /model_info/stats - Model statistics and counts
    - GET /model_info/configurations - All model configurations for debugging
    - GET /model_info/debug/metadata-adapter - Debug endpoint for adapter testing
    - GET /model_info/{model_id} - Comprehensive model information
    - GET /model_info/{model_id}/chat-template - Chat template information
    - GET /model_info/aliases - Model aliases
    - GET /model_info/validate - Model validation
    """

    def __init__(
        self, model_registry: ModelRegistry, gateway_config: Any | None = None
    ):
        self.registry = model_registry
        self.gateway_config = gateway_config

        # Load engine specifications and model type patterns from config
        self._load_configuration()

    def _load_configuration(self):
        """Load configuration from gateway config or use minimal fallbacks"""
        if self.gateway_config:
            # Load inference engine specifications from config
            self.inference_engine_specs = getattr(
                self.gateway_config, "inference_engines", {}
            )

            # Load model type patterns from config
            self.model_type_patterns = getattr(
                self.gateway_config, "model_type_patterns", {}
            )
        else:
            # Minimal fallbacks if no config available
            logger.warning("No gateway config available, using minimal fallbacks")
            self.inference_engine_specs = {}
            self.model_type_patterns = {}

    def _has_working_chat_template(self, model_path: str, model_format: str) -> bool:
        """
        Check if model has a working chat template using djinn inspector results.

        Args:
            model_path: Path to the model directory or file
            model_format: Model format (awq, gptq, gguf, etc.)

        Returns:
            True if model has a working chat template, False otherwise
        """
        # Find the model metadata from the registry instead of re-analyzing the file
        model_metadata = None
        for metadata in self.registry.models_to_metadata.values():
            if metadata.path == model_path:
                model_metadata = metadata
                break

        if not model_metadata:
            logger.warning(f"Model metadata not found for path: {model_path}")
            return False

        # Use djinn inspector results for chat template detection
        try:
            # Import and use the appropriate djinn inspector
            if model_format == "gguf":
                from inference_djinn.engines.gguf.inspector import (
                    detect_chat_template_support,
                )

                # Get chat template support from GGUF inspector
                chat_template_support = detect_chat_template_support(model_path)
                has_working_template = chat_template_support.get(
                    "has_chat_template", False
                )
                template_type = chat_template_support.get("template_type", "none")

                logger.debug(
                    "Model %s chat template support: %s, type: %s",
                    model_path,
                    has_working_template,
                    template_type,
                )
                return has_working_template
            elif model_format in ["awq", "gptq", "hf"]:
                from inference_djinn.engines.vllm.inspector import get_vllm_model_info

                # Get model info from vLLM inspector
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
            else:
                # For other formats, return False as we don't have djinn inspectors
                return False

        except Exception as e:
            logger.error(f"Error checking chat template support for {model_path}: {e}")
            return False

    def get_chat_template_from_config(
        self, model_id: str, model_config: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Get chat template information from YAML configuration.

        This method reads template configuration from model_loaders.yaml,
        it does NOT analyze model files.

        Args:
            model_id: Model identifier
            model_config: Model configuration from model_loaders.yaml

        Returns:
            Dictionary with chat template configuration from YAML
        """
        if not isinstance(model_config, dict):
            return {
                "exists": False,
                "content": None,
                "supports_system_role": False,
                "source": None,
            }

        # Check metadata first (current schema), then info (legacy)
        model_info = model_config.get("metadata") or model_config.get("info") or {}
        logger.debug(
            "📄 get_chat_template_from_config(%s): reading from YAML config - "
            "family=%s, input_schema=%s",
            model_id,
            model_info.get("family"),
            model_info.get("input_schema"),
        )

        # Priority 1: Explicit chat_template in config
        if "chat_template" in model_info:
            return {
                "exists": True,
                "content": model_info["chat_template"],
                "supports_system_role": model_info.get("supports_system_role", True),
                "source": "yaml_config",
            }

        # Priority 2: Infer from model_family and input_schema
        model_family = safe_lower(model_info.get("family"))
        input_schema = model_info.get("input_schema") or "prompt"

        # Models with input_schema='messages' have chat templates
        if input_schema == "messages":
            template_type = model_family if model_family else "default"
            return {
                "exists": True,
                "content": f"{template_type}_chat_template",
                "supports_system_role": True,
                "source": "inferred_from_family",
            }

        # Priority 3: Models with 'prompt' input_schema but known families
        if model_family in ["llama", "mistral", "qwen", "deepseek"]:
            return {
                "exists": True,
                "content": f"{model_family}_chat_template",
                "supports_system_role": False,
                "source": "inferred_from_family",
            }

        # Default: No chat template
        return {
            "exists": False,
            "content": None,
            "supports_system_role": False,
            "source": None,
        }

    def get_model_type_from_config(
        self, model_id: str, model_config: dict[str, Any]
    ) -> str:
        """
        Get model type from YAML configuration, with fallback to model file inspection.

        Primary source is YAML config; only inspects files if config is incomplete.

        Args:
            model_id: Model identifier
            model_config: Model configuration from model_loaders.yaml

        Returns:
            Model type string (e.g., 'llama', 'mistral', 'qwen', 'default')
        """
        from src.utils.model_utils import detect_model_type

        # Get model path for potential inference_djinn inspection
        model_path = model_config.get("path") if model_config else None

        return detect_model_type(model_id, model_config, model_path)

    def get_inference_engine_info(self, model_id: str) -> dict[str, Any]:
        """
        Get inference engine information for a model based on input_schema from
        metadata.

        Args:
            model_id: Model identifier

        Returns:
            Dictionary with inference engine information
        """
        metadata = self.registry.get_model_metadata(model_id)
        if not metadata:
            return {}

        model_format = metadata.format

        # Get engine specifications from config (format-based only)
        engine_specs = self.inference_engine_specs.get(model_format, {})

        # STRICT: Use input_schema from metadata - no overrides, no degradation
        input_schema = getattr(
            metadata, "input_schema", "messages"
        )  # Default to messages if not set
        uses_chat_template = input_schema == "messages"

        # Build specification dictionary
        specification = {
            "input_format": input_schema,
            "uses_chat_template": uses_chat_template,
            "expected_field": input_schema,
        }

        # Build inference engine info - NO degradation fields
        inference_info = {
            "engine_name": engine_specs.get("engine_name", "unknown"),
            "format": model_format,
            "input_format": input_schema,
            "expected_field": input_schema,
            "uses_chat_template": uses_chat_template,
            "specification": specification,
        }

        return inference_info

    def get_comprehensive_model_metadata(self, model_id: str) -> dict[str, Any] | None:
        """
        Get comprehensive metadata for a single model.

        Args:
            model_id: Model identifier

        Returns:
            Dictionary with comprehensive model metadata, or None if not found
        """
        # Use get_model_info which reads from config directly
        model_info = self.registry.get_model_info(model_id)
        if not model_info:
            return None

        # Get model config for additional fields
        model_config_key = self.registry.find_config_key_for_openai_id(model_id)
        if not model_config_key:
            return None

        models_data = self.registry.model_loaders_config.get("models", {})
        model_config = models_data.get(model_config_key, {})
        # Check metadata first (current schema), then info (legacy)
        model_info_section = (
            model_config.get("metadata") or model_config.get("info") or {}
        )

        logger.debug(
            f"🔍 get_comprehensive_model_metadata({model_id}): "
            f"config_key={model_config_key}, has_config={bool(model_config)}, "
            f"has_info={bool(model_info_section)}"
        )

        # Path extraction for models
        model_path = model_info_section.get("path", "")

        # Get additional information using YAML-based methods (no file system access)
        chat_template_info = self.get_chat_template_from_config(model_id, model_config)
        inference_engine_info = self.get_inference_engine_info(model_id)
        model_type = self.get_model_type_from_config(model_id, model_config)

        # Build comprehensive metadata from model_info and config
        # Convert parameters to string if it's a number
        parameters = model_info_section.get("parameters")
        if parameters is not None and not isinstance(parameters, str):
            parameters = str(parameters)

        # Extract resource usage from highest context profile
        ram_usage = 0
        vram_usage = 0
        context_length = None

        profiles = model_config.get("profiles", {})
        if profiles:
            # Find highest context profile for resource information
            profile_keys = [key for key in profiles.keys() if key.isdigit()]
            if profile_keys:
                highest_context_key = max(profile_keys, key=int)
                highest_profile = profiles[highest_context_key]
                resources = highest_profile.get("resources", {})
                ram_usage = resources.get("ram_mb", 0)
                vram_usage = resources.get("vram_mb", 0)
                context_length = int(highest_context_key)

        # Build parameter defaults from base loader config
        base_loader = model_config.get("base_loader", {})
        parameter_defaults = {
            "temperature": 0.7,
            "top_p": 0.9,
            "max_tokens": 2048,
            **base_loader,  # Include loader-specific defaults
        }

        # Build middleware configuration
        input_schema = model_info_section.get("input_schema", "prompt")
        model_family = model_info_section.get("family", "default")
        preserve_personality = model_family != "default" and input_schema != "messages"

        middleware_config = {
            "preserve_personality": preserve_personality,
            "supports_system_role": chat_template_info.get(
                "supports_system_role", False
            )
            if chat_template_info
            else False,
            "model_family": model_family,
            # Sticky routing policy (Stargate):
            # - True (default): ∀ model, ∃! gateway where model is loaded
            # - False: model may be loaded on multiple gateways
            "sticky": model_info_section.get("sticky", True),
        }

        # Build supported parameters list based on format
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

        comprehensive_metadata = {
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
            # New fields required by Stargate
            "input_schema": input_schema,
            "parameter_defaults": parameter_defaults,
            "middleware_config": middleware_config,
            "ram_usage": ram_usage,
            "vram_usage": vram_usage,
            "context_length": context_length,
            "supported_parameters": supported_parameters,
        }

        return comprehensive_metadata

    def get_all_models_metadata(self) -> dict[str, dict[str, Any]]:
        """
        Get comprehensive metadata for all models.

        Returns:
            Dictionary mapping model IDs to their comprehensive metadata
        """
        all_metadata = {}

        models_list = self.registry.list_models()
        logger.debug(f"list_models() returned {len(models_list)} models")

        for model_info in models_list:
            logger.debug(f"Processing {model_info.id}")
            metadata = self.get_comprehensive_model_metadata(model_info.id)
            logger.debug(f"Got metadata for {model_info.id}: {metadata is not None}")
            if metadata:
                all_metadata[model_info.id] = metadata

        logger.debug(f"Returning {len(all_metadata)} models")
        return all_metadata
