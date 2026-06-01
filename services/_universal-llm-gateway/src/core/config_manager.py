"""
Configuration manager for model_loaders.yaml with validation and atomic writes.

Provides centralized, thread-safe configuration management with:
- Schema validation against Pydantic models
- Atomic writes with file locking
- Hot-reload support for live gateway updates
- GPTQ/AWQ normalization to HF loader format
"""

import os
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import ValidationError
from universal_logging import get_logger

try:
    from ..schemas.yaml_config import (
        GGUFModelConfig,
        HFModelConfig,
        ResourceManagement,
    )
    from ..utils.examples import ExampleGenerator
    from .file_locker import FileLock
except ImportError:
    from src.core.file_locker import FileLock
    from src.schemas.yaml_config import (
        GGUFModelConfig,
        HFModelConfig,
        ResourceManagement,
    )
    from src.utils.examples import ExampleGenerator

logger = get_logger(__name__)


def _is_type_transition_allowed(existing: Any, new: Any) -> bool:
    """
    Check if type transition is allowed for configuration merging.

    Allows specific type transitions that are common in configuration updates:
    - None -> any type (common initialization pattern)
    - any type -> None (for optional fields that allow null)
    - int -> str (e.g., 33000000000 -> "33B")
    - str -> int (e.g., "33B" -> 33000000000)

    Args:
        existing: Current value
        new: New value to merge

    Returns:
        True if transition is allowed, False otherwise
    """
    # Allow None -> any type
    if existing is None:
        return True

    # Allow any type -> None (for optional fields that allow null)
    if new is None:
        return True

    # Allow int -> str (for parameters like "33B")
    if isinstance(existing, int) and isinstance(new, str):
        return True

    # Allow str -> int (for parameters like "33B" -> 33000000000)
    if isinstance(existing, str) and isinstance(new, int):
        return True

    return False


def deep_merge_dict(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    """
    Deep merge two dictionaries, preserving existing fields and updating only explicitly provided ones.

    This function implements a merge strategy that:
    1. Preserves existing fields not provided in the update
    2. Updates only explicitly set fields
    3. Merges nested objects recursively
    4. Handles profiles, loader configs, and resources appropriately

    Args:
        base: Base dictionary to merge into
        update: Dictionary with updates to apply

    Returns:
        Merged dictionary with updates applied

    Raises:
        ValueError: If there are type conflicts between base and update values
    """
    result = base.copy()

    for key, value in update.items():
        if key not in result:
            # New key - add it
            result[key] = value
        elif isinstance(result[key], dict) and isinstance(value, dict):
            # Both are dicts - merge recursively
            result[key] = deep_merge_dict(result[key], value)
        elif isinstance(result[key], list) and isinstance(value, list):
            # Both are lists - merge lists (extend with new items)
            # For model configs, we typically want to replace lists rather than extend
            result[key] = value
        elif _is_type_transition_allowed(result[key], value):
            # Allow specific type transitions (None -> any, int <-> str)
            result[key] = value
        elif type(result[key]) is type(value):
            # Same types - update the value
            result[key] = value
        else:
            # Type conflict - raise error
            raise ValueError(
                f"Type conflict for key '{key}': "
                f"existing type {type(result[key]).__name__}, "
                f"update type {type(value).__name__}"
            )

    return result


class ConfigValidationError(Exception):
    """Raised when configuration validation fails"""

    pass


class ValidationContext(Enum):
    """Context for validation operations"""

    NEW = "new"
    UPDATE = "update"


@dataclass
class ModelOperationResult:
    """Result of a model operation"""

    operation: Literal["created", "updated_by_key", "updated_by_path"]
    model_key: str
    requested_key: str | None = None
    _custom_message: str | None = None

    @property
    def message(self) -> str:
        """Generate or return custom message"""
        if self._custom_message:
            return self._custom_message

        if self.operation == "created":
            return f"Created new model '{self.model_key}' with unique path"
        elif self.operation == "updated_by_key":
            return f"Updated existing model '{self.model_key}'"
        else:  # updated_by_path
            return (
                f"Updated existing model '{self.model_key}' with same path "
                f"(requested key '{self.requested_key}' was ignored due to path-based update)"
            )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses"""
        result = {
            "status": "success",
            "model_key": self.model_key,
            "operation": self.operation,
            "message": self.message,
        }

        if self.requested_key and self.requested_key != self.model_key:
            result["requested_key"] = self.requested_key

        return result


class ConfigManager:
    """
    Centralized configuration manager for model_loaders.yaml.

    Handles all I/O operations with schema validation, atomic writes,
    file locking, and hot-reload support.
    """

    def __init__(self, config_path: str | Path = "config/model_loaders.yaml"):
        """
        Initialize configuration manager.

        Args:
            config_path: Path to model_loaders.yaml file
        """
        self.config_path = Path(config_path)
        self.lock_path = self.config_path.with_suffix(".yaml.lock")
        self.example_generator = ExampleGenerator()

    def load_config(self) -> dict[str, Any]:
        """
        Load configuration from file without validation.

        Returns:
            Raw configuration dictionary

        Raises:
            FileNotFoundError: If config file doesn't exist
            yaml.YAMLError: If YAML is malformed
        """
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config file not found: {self.config_path}")

        try:
            with open(self.config_path, encoding="utf-8") as f:
                config = yaml.safe_load(f)

            logger.debug(f"Loaded config from {self.config_path}")
            return config

        except yaml.YAMLError as e:
            raise ConfigValidationError(f"Invalid YAML in {self.config_path}: {e}")

    def load_and_validate(self) -> dict[str, Any]:
        """
        Load and validate configuration against schemas.

        Returns:
            Validated configuration dictionary

        Raises:
            ConfigValidationError: If validation fails
        """
        config = self.load_config()
        self.validate_config(config)
        return config

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

    def write_config(self, config: dict[str, Any], validate: bool = True) -> None:
        """
        Write configuration to file with atomic write and locking.

        Args:
            config: Configuration dictionary to write
            validate: If True, validate before writing

        Raises:
            ConfigValidationError: If validation fails
            IOError: If write fails
        """
        # Validate first if requested
        if validate:
            self.validate_config(config)

        # Use file lock to prevent concurrent writes
        with FileLock(self.lock_path, timeout=30.0):
            # Write to temporary file first
            temp_path = self.config_path.with_suffix(".yaml.tmp")

            try:
                with open(temp_path, "w", encoding="utf-8") as f:
                    yaml.dump(
                        config,
                        f,
                        default_flow_style=False,
                        sort_keys=False,
                        allow_unicode=True,
                    )

                # Atomic replace
                os.replace(temp_path, self.config_path)
                logger.info(f"Config written successfully to {self.config_path}")

            except Exception as e:
                # Clean up temp file on failure
                if temp_path.exists():
                    temp_path.unlink()
                raise OSError(f"Failed to write config: {e}")

    def _normalize_path(self, path: str) -> str:
        """
        Normalize a file path for comparison.

        Args:
            path: File path to normalize

        Returns:
            Normalized absolute path

        Note:
            This method assumes Unix-like path semantics. On Windows, UNC paths
            may not normalize as expected. Empty paths return empty string,
            which disables path-based deduplication for that model.
        """
        if not path:
            return ""
        return os.path.abspath(os.path.expanduser(path))

    def _sort_profiles_numerically(self, model_config: dict[str, Any]) -> None:
        """
        Sort profiles in a model configuration numerically (in-place).

        Profile keys are expected to be string representations of integers
        (e.g., '2048', '4096', '8192'). This method sorts them numerically
        so they appear in ascending order in the YAML file.

        Args:
            model_config: Model configuration dictionary to sort profiles in

        Note:
            Modifies model_config in-place. Only sorts if 'profiles' key exists.
        """
        # GGUF models have profiles
        if "profiles" in model_config and isinstance(model_config["profiles"], dict):
            profiles = model_config["profiles"]

            # Sort profile keys numerically
            try:
                sorted_keys = sorted(profiles.keys(), key=lambda x: int(x))
                model_config["profiles"] = {k: profiles[k] for k in sorted_keys}
                logger.debug(f"Sorted profiles numerically: {sorted_keys}")
            except ValueError:
                # If any key can't be converted to int, keep original order
                logger.debug(
                    "Could not sort profiles numerically (non-integer keys found)"
                )
                pass

    def upsert_model(
        self,
        model_key: str,
        model_config: dict[str, Any],
        *,
        allow_path_deduplication: bool = True,
        allow_key_overwrite: bool = True,
    ) -> ModelOperationResult:
        """
        Insert or update a model configuration with smart path deduplication.

        This method implements smart duplicate detection based on model paths.
        If a model with the same path already exists (even with a different key),
        it will update that existing model instead of creating a duplicate.

        Args:
            model_key: Desired model key
            model_config: Complete model configuration
            allow_path_deduplication: If True, update existing model with same path
            allow_key_overwrite: If True, allow overwriting model with same key

        Returns:
            ModelOperationResult with operation type and actual key used

        Raises:
            ConfigValidationError: If config is invalid
        """
        config = self.load_config()

        # Check if model key already exists
        if model_key in config["models"]:
            if not allow_key_overwrite:
                raise ConfigValidationError(
                    f"Model '{model_key}' already exists. "
                    f"Use allow_key_overwrite=True to replace."
                )
            else:
                # Direct key-based update: merge existing model with new config
                logger.info(f"Updating existing model '{model_key}'")
                try:
                    config["models"][model_key] = deep_merge_dict(
                        config["models"][model_key], model_config
                    )
                except ValueError as e:
                    raise ConfigValidationError(
                        f"Failed to merge configuration for model '{model_key}': {e}"
                    )
                # Sort profiles numerically before validation
                self._sort_profiles_numerically(config["models"][model_key])
                self.validate_config(
                    config,
                    ValidationContext.UPDATE,
                    {model_key},
                    validate_only={model_key},
                )
                self.write_config(config, validate=False)

                return ModelOperationResult(
                    operation="updated_by_key", model_key=model_key
                )

        model_path = model_config.get("info", {}).get("path")

        # Check for path-based deduplication
        if model_path and allow_path_deduplication:
            # Find existing model with same path
            normalized_search_path = self._normalize_path(model_path)
            existing_key = None

            for key, existing_model_config in config.get("models", {}).items():
                if not isinstance(existing_model_config, dict):
                    continue

                info = existing_model_config.get("info", {})
                existing_path = info.get("path")
                if (
                    existing_path
                    and self._normalize_path(existing_path) == normalized_search_path
                ):
                    existing_key = key
                    break

            if existing_key:
                # Path-based update: update existing model with same path
                logger.info(
                    f"Found existing model '{existing_key}' with same path '{model_path}'. "
                    f"Updating '{existing_key}' instead of creating '{model_key}'."
                )
                # Deep merge the existing config with the new config
                try:
                    config["models"][existing_key] = deep_merge_dict(
                        config["models"][existing_key], model_config
                    )
                except ValueError as e:
                    raise ConfigValidationError(
                        f"Failed to merge configuration for model '{existing_key}': {e}"
                    )
                # Sort profiles numerically before validation
                self._sort_profiles_numerically(config["models"][existing_key])
                self.validate_config(
                    config,
                    ValidationContext.UPDATE,
                    {existing_key},
                    validate_only={existing_key},
                )
                self.write_config(config, validate=False)

                return ModelOperationResult(
                    operation="updated_by_path",
                    model_key=existing_key,
                    requested_key=model_key,
                )

        # Add new model if no existing model found with same path
        config["models"][model_key] = model_config
        # Sort profiles numerically before validation
        self._sort_profiles_numerically(config["models"][model_key])
        self.validate_config(config, ValidationContext.NEW, validate_only={model_key})
        self.write_config(config, validate=False)

        logger.info(f"Added model '{model_key}' to configuration")
        return ModelOperationResult(operation="created", model_key=model_key)

    def get_model(self, model_key: str) -> dict[str, Any] | None:
        """
        Get a model configuration by key.

        Args:
            model_key: Model configuration key

        Returns:
            Model configuration dictionary or None if not found
        """
        config = self.load_config()
        return config["models"].get(model_key)

    def delete_model(self, model_key: str) -> None:
        """
        Delete a model from configuration.

        Args:
            model_key: Key of model to delete

        Raises:
            ConfigValidationError: If model doesn't exist
        """
        config = self.load_config()

        if model_key not in config["models"]:
            raise ConfigValidationError(f"Model '{model_key}' not found")

        # Remove model
        del config["models"][model_key]

        # Write atomically (write_config handles locking)
        self.write_config(config, validate=True)

        logger.info(f"Deleted model '{model_key}' from configuration")

    def list_models(self) -> dict[str, dict[str, Any]]:
        """
        List all models in configuration.

        Returns:
            Dictionary of model_key -> model_config
        """
        config = self.load_config()
        return config["models"]

    def get_config_version(self) -> float:
        """
        Get configuration file modification time as version.

        Returns:
            mtime timestamp for optimistic concurrency control
        """
        if not self.config_path.exists():
            return 0.0
        return self.config_path.stat().st_mtime

    def reload(self, app_state: Any = None) -> set[str]:
        """
        Reload configuration and update live application state.

        Args:
            app_state: FastAPI app.state object (optional)

        Returns:
            Set of model keys that changed

        Raises:
            ConfigValidationError: If new config is invalid
        """
        # Load and validate new config
        new_config = self.load_and_validate()

        if app_state is None:
            logger.info("Config reloaded (no app_state provided for live update)")
            return set()

        # Get old model keys
        old_models = set(app_state.model_registry.model_loaders_config["models"].keys())
        new_models = set(new_config["models"].keys())

        # Update registry in-place
        app_state.model_registry.model_loaders_config = new_config

        # Calculate changes
        added = new_models - old_models
        removed = old_models - new_models
        possibly_modified = old_models & new_models
        changed = added | removed

        # Emit event for worker reconciliation
        if hasattr(app_state, "event_bus") and app_state.event_bus:
            event_data = {
                "type": "config_reloaded",
                "models_added": list(added),
                "models_removed": list(removed),
                "models_possibly_modified": list(possibly_modified),
                "timestamp": time.time(),
            }
            app_state.event_bus.emit(event_data)
            logger.info(
                f"Emitted config_reloaded event: {len(added)} added, {len(removed)} removed"
            )

        logger.info(
            f"Config reloaded: {len(added)} added, {len(removed)} removed, {len(possibly_modified)} existing"
        )

        return changed

    def get_example(self, format_type: str) -> dict[str, Any]:
        """
        Get example configuration for a format.

        Args:
            format_type: Model format ('gguf' or 'hf')

        Returns:
            Example configuration dictionary
        """
        return self.example_generator.get_example_dict(format_type)

    def get_schema_info(self, format_type: str) -> dict[str, Any]:
        """
        Get schema metadata for a format.

        Args:
            format_type: Model format ('gguf' or 'hf')

        Returns:
            Dictionary with schema_fields, required_fields, optional_fields
        """
        return self.example_generator.get_schema_info(format_type)
