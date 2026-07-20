"""CRUD mixin for ConfigManager model entries.

Provides upsert/get/delete/list against the in-memory model_loaders document
with deep-merge semantics on update.
"""

from typing import Any

from universal_logging import get_logger

from .merge import deep_merge_dict
from .types import ConfigValidationError, ModelOperationResult, ValidationContext

logger = get_logger(__name__)


class ConfigCRUDMixin:
    """Mixin providing model entry CRUD for ConfigManager."""

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
