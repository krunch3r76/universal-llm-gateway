"""Validation and normalization mixin for ConfigManager.

Owns schema validation against Pydantic loader configs, GPTQ/AWQ normalization,
and uniqueness checks for openai IDs and model paths.
"""

from typing import Any

from pydantic import ValidationError
from universal_logging import get_logger

try:
    from ...schemas.yaml_config import (
        GGUFModelConfig,
        HFModelConfig,
        ResourceManagement,
    )
except ImportError:
    from src.schemas.yaml_config import (
        GGUFModelConfig,
        HFModelConfig,
        ResourceManagement,
    )

from .types import ConfigValidationError, ValidationContext

logger = get_logger(__name__)


class ConfigValidationMixin:
    """Mixin providing model_loaders.yaml validation helpers for ConfigManager."""

    def validate_config(
        self,
        config: dict[str, Any],
        context: ValidationContext = ValidationContext.NEW,
        exclude_model_keys: set[str] | None = None,
        validate_only: set[str] | None = None,
    ) -> None:
        """
        Validate configuration against Pydantic schemas with context-aware uniqueness checks.

        Args:
            config: Configuration dictionary to validate
            context: What operation is being performed
            exclude_model_keys: Model keys to exclude from uniqueness checks
                               (useful for updates where the model being updated
                               shouldn't conflict with itself)
            validate_only: If provided, only validate schema for these specific models.
                          This allows adding/updating models without revalidating all
                          existing models (useful when schema changes leave old models invalid)

        Raises:
            ConfigValidationError: If validation fails with detailed errors
        """
        # Validate top-level structure
        if "models" not in config:
            raise ConfigValidationError("Config must contain 'models' section")

        if "resource_management" not in config:
            raise ConfigValidationError(
                "Config must contain 'resource_management' section"
            )

        # Validate resource_management
        try:
            ResourceManagement(**config["resource_management"])
        except ValidationError as e:
            raise ConfigValidationError(f"Invalid resource_management: {e}")

        # Validate each model
        models = config["models"]
        errors = []

        for model_key, model_config in models.items():
            # Skip aliases (string values)
            if not isinstance(model_config, dict):
                continue

            # Skip models not in validate_only set (selective validation)
            if validate_only is not None and model_key not in validate_only:
                logger.debug(
                    f"Skipping schema validation for existing model '{model_key}'"
                )
                continue

            try:
                self._validate_model_config(model_key, model_config)
            except ConfigValidationError as e:
                errors.append(f"Model '{model_key}': {e}")

        if errors:
            error_msg = "Configuration validation failed:\n" + "\n".join(errors)
            raise ConfigValidationError(error_msg)

        # Context-aware uniqueness validation
        if context == ValidationContext.NEW:
            # Full uniqueness validation for new models
            self._validate_unique_openai_ids(models)
            self._validate_unique_paths(models)
        elif context == ValidationContext.UPDATE:
            # Only validate uniqueness against OTHER models (exclude the one being updated)
            exclude_keys = exclude_model_keys or set()
            self._validate_unique_openai_ids(models, exclude_keys)
            self._validate_unique_paths(models, exclude_keys)

    def _validate_model_config(
        self, model_key: str, model_config: dict[str, Any]
    ) -> None:
        """
        Validate a single model configuration.

        Args:
            model_key: Model configuration key
            model_config: Model configuration dictionary

        Raises:
            ConfigValidationError: If validation fails
        """
        # Get format from info section
        if "info" not in model_config:
            raise ConfigValidationError("Model must have 'info' section")

        format_type = model_config["info"].get("format")
        if not format_type:
            raise ConfigValidationError("Model info must specify 'format'")

        # Normalize GPTQ/AWQ to HF for validation
        normalized_config = self._normalize_model_config(model_config, format_type)
        normalized_format = normalized_config["info"]["format"]

        # Validate against appropriate schema
        try:
            if normalized_format == "gguf":
                GGUFModelConfig(**normalized_config)
            elif normalized_format == "hf":
                HFModelConfig(**normalized_config)
            else:
                raise ConfigValidationError(
                    f"Unsupported format '{format_type}'. Supported: gguf, hf, gptq, awq"
                )
        except ValidationError as e:
            # Format validation errors nicely
            error_details = []
            for error in e.errors():
                loc = " -> ".join(str(x) for x in error["loc"])
                msg = error["msg"]
                error_details.append(f"  {loc}: {msg}")

            raise ConfigValidationError(
                "Schema validation failed:\n" + "\n".join(error_details)
            )

    def _normalize_model_config(
        self, model_config: dict[str, Any], format_type: str
    ) -> dict[str, Any]:
        """
        Normalize GPTQ/AWQ configs to HF format for validation.

        GPTQ and AWQ use the same vLLM/transformers stack as HF models,
        so we normalize them to HF format with appropriate loader config.

        Args:
            model_config: Model configuration
            format_type: Original format type

        Returns:
            Normalized configuration (copy)
        """
        # Only normalize gptq and awq
        if format_type not in ["gptq", "awq"]:
            return model_config

        # Create a copy to avoid modifying original
        import copy

        normalized = copy.deepcopy(model_config)

        # Change format to 'hf' for validation
        normalized["info"]["format"] = "hf"

        # Ensure base_loader config exists and is HF-compatible
        if "base_loader" not in normalized:
            # Minimal safe defaults for catalog generation
            # Real values should be measured/configured per model
            normalized["base_loader"] = {
                "trust_remote_code": False,  # SECURITY: Never trust remote code
                "disable_custom_all_reduce": True,  # Stability
                "disable_log_stats": True,  # Reduce noise
            }

        # Remove legacy GPTQ-specific keys that don't belong in HF schema
        legacy_keys = ["device_map", "torch_dtype", "low_cpu_mem_usage"]
        base_loader_config = normalized.get("base_loader", {})
        for key in legacy_keys:
            base_loader_config.pop(key, None)

        # Ensure profiles exist for HF models
        if "profiles" not in normalized:
            # Create default profile based on base_loader max_model_len
            max_len = base_loader_config.get("max_model_len", 8192)
            normalized["profiles"] = {
                str(max_len): {
                    "loader": {"max_model_len": max_len},
                    "resources": {"ram_mb": None, "vram_mb": None},
                    "default": True,
                }
            }

        return normalized

    def _validate_unique_openai_ids(
        self, models: dict[str, Any], exclude_keys: set[str] | None = None
    ) -> None:
        """
        Validate that all openai_api_fields.id values are unique.

        Args:
            models: Models dictionary
            exclude_keys: Model keys to exclude from uniqueness checks

        Raises:
            ConfigValidationError: If duplicate IDs found
        """
        openai_ids = {}
        exclude_keys = exclude_keys or set()

        for model_key, model_config in models.items():
            if not isinstance(model_config, dict) or model_key in exclude_keys:
                continue

            model_info = model_config.get("info", {})
            openai_fields = model_info.get("openai_api_fields", {})
            openai_id = openai_fields.get("id")

            if openai_id:
                if openai_id in openai_ids:
                    raise ConfigValidationError(
                        f"Duplicate openai_api_fields.id '{openai_id}' found in "
                        f"models '{openai_ids[openai_id]}' and '{model_key}'"
                    )
                openai_ids[openai_id] = model_key

    def _validate_unique_paths(
        self, models: dict[str, Any], exclude_keys: set[str] | None = None
    ) -> None:
        """
        Validate that all model paths are unique.

        Args:
            models: Models dictionary
            exclude_keys: Model keys to exclude from uniqueness checks

        Raises:
            ConfigValidationError: If duplicate paths found
        """
        paths = {}
        exclude_keys = exclude_keys or set()

        for model_key, model_config in models.items():
            if not isinstance(model_config, dict) or model_key in exclude_keys:
                continue

            model_info = model_config.get("info", {})
            model_path = model_info.get("path")

            if model_path:
                # Normalize path for comparison
                normalized_path = self._normalize_path(model_path)

                if normalized_path in paths:
                    raise ConfigValidationError(
                        f"Duplicate model path '{model_path}' found in "
                        f"models '{paths[normalized_path]}' and '{model_key}'"
                    )
                paths[normalized_path] = model_key
