"""Load/write/reload I/O mixin for ConfigManager.

Handles YAML load, atomic write via FileLock, profile sorting, and hot-reload
of model_loaders.yaml from disk.
"""

import os
import time
from typing import Any

import yaml
from universal_logging import get_logger

try:
    from ..file_locker import FileLock
except ImportError:
    from src.core.file_locker import FileLock

from .types import ConfigValidationError

logger = get_logger(__name__)


class ConfigIOMixin:
    """Mixin providing YAML I/O and reload for ConfigManager."""

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
