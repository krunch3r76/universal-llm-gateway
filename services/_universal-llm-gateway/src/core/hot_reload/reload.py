"""
Configuration reload logic for hot reload functionality.

Handles parsing, validation, merging, and rollback of configuration files.
"""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from universal_hot_reload import read_text_preserving_timestamps
from universal_logging import get_logger

try:
    from ..config_manager import deep_merge_dict
    from ..hot_reload_metrics import hot_reload_metrics
except ImportError:
    from src.core.config_manager import deep_merge_dict
    from src.core.hot_reload_metrics import hot_reload_metrics

from .types import ReloadEvent

logger = get_logger(__name__)


class ConfigReloader:
    """Handles configuration file reload operations.

    Provides:
    - File parsing (YAML, JSON)
    - Security validation (path, size)
    - Deep merge with existing config
    - Automatic rollback on failure
    """

    def __init__(
        self,
        model_registry,
        allowed_paths: list[str],
        max_file_size_mb: int,
    ):
        """Initialize config reloader.

        Args:
            model_registry: Model registry instance
            allowed_paths: List of allowed path prefixes for security
            max_file_size_mb: Maximum file size to process
        """
        self.model_registry = model_registry
        self.allowed_paths = allowed_paths
        self.max_file_size_mb = max_file_size_mb

        # Configuration backup for rollback
        self._config_backups: dict[str, dict[str, Any]] = {}

    async def execute_reload(self, file_path: str) -> ReloadEvent:
        """Execute configuration reload.

        Args:
            file_path: Path to the configuration file

        Returns:
            ReloadEvent with success/failure status
        """
        start_time = time.time()
        path = Path(file_path)
        file_ext = path.suffix

        # Initialize before try block to ensure defined in except block
        model_key: str = self._extract_model_key(path)
        original_config: dict[str, Any] = self._get_current_config(model_key)

        try:
            logger.info(f"Reloading configuration from {file_path}")

            # Store original configuration for rollback
            self._config_backups[model_key] = original_config

            # Record reload attempt
            hot_reload_metrics.record_reload_attempt(model_key, file_ext)

            # Security validation
            self._validate_path_allowed(path)
            self._validate_file_size(path)

            # Parse the configuration file
            config_data = self._parse_config_file(path)

            # Update the model in memory (with deep merge)
            await self._update_model_in_memory(model_key, config_data)

            # Validate the merged configuration
            merged_config = self._get_merged_config(model_key)
            self._validate_model_config(merged_config)

            # Calculate duration
            duration_ms = (time.time() - start_time) * 1000

            # Record success
            hot_reload_metrics.record_reload_success(
                model_key, file_ext, duration_ms / 1000.0
            )

            logger.info(
                f"✅ Reloaded model '{model_key}' from {file_path} "
                f"(took {duration_ms:.1f}ms)"
            )

            return ReloadEvent(
                file_path=file_path,
                model_key=model_key,
                success=True,
                timestamp=datetime.now(),
                duration_ms=duration_ms,
            )

        except Exception as e:
            # Automatic rollback on failure
            try:
                await self._rollback_config(model_key, original_config)
                logger.info(f"Rolled back configuration for {model_key}")
            except Exception as rollback_error:
                logger.error(f"Failed to rollback: {rollback_error}")

            duration_ms = (time.time() - start_time) * 1000
            hot_reload_metrics.record_reload_failure(
                model_key, file_ext, duration_ms / 1000.0
            )

            logger.error(f"Failed to reload configuration: {e}")

            return ReloadEvent(
                file_path=file_path,
                model_key=model_key,
                success=False,
                timestamp=datetime.now(),
                error=str(e),
                duration_ms=duration_ms,
            )

    def _extract_model_key(self, file_path: Path) -> str:
        """Extract model key from file path."""
        if file_path.name == "model_loaders.yaml":
            return "model_loaders"
        return file_path.stem

    def _validate_path_allowed(self, file_path: Path):
        """Validate file path is within allowed directories."""
        file_path_str = str(file_path.resolve())
        for allowed_path in self.allowed_paths:
            allowed_resolved = str(Path(allowed_path).resolve())
            if file_path_str.startswith(allowed_resolved):
                return
        raise ValueError(f"File path '{file_path}' not in allowed paths")

    def _validate_file_size(self, file_path: Path):
        """Validate file size is within limits."""
        file_size_mb = file_path.stat().st_size / (1024 * 1024)
        if file_size_mb > self.max_file_size_mb:
            raise ValueError(
                f"File size {file_size_mb:.1f}MB exceeds "
                f"limit {self.max_file_size_mb}MB"
            )

    def _parse_config_file(self, file_path: Path) -> dict[str, Any]:
        """Parse configuration file without modifying timestamps.
        
        Preserves file access/modification times to prevent editor notifications
        about spurious changes when hot-reload reads config files.
        """
        import yaml

        # Read without triggering editor change notifications
        content = read_text_preserving_timestamps(file_path)

        if file_path.suffix in [".yaml", ".yml"]:
            return yaml.safe_load(content) or {}
        elif file_path.suffix == ".json":
            return json.loads(content) or {}
        else:
            raise ValueError(f"Unsupported format: {file_path.suffix}")

    def _validate_model_config(self, config_data: dict[str, Any]):
        """Validate model configuration."""
        if not isinstance(config_data, dict):
            raise ValueError("Configuration must be a dictionary")

        if "models" in config_data:
            if not isinstance(config_data["models"], dict):
                raise ValueError("'models' section must be a dictionary")

    async def _update_model_in_memory(self, model_key: str, new_config: dict[str, Any]):
        """Update model configuration in memory with deep merge."""
        if hasattr(self.model_registry, "model_loaders_config"):
            if model_key == "model_loaders":
                self.model_registry.model_loaders_config = new_config
            else:
                if "models" in self.model_registry.model_loaders_config:
                    if model_key in self.model_registry.model_loaders_config["models"]:
                        existing = self.model_registry.model_loaders_config["models"][
                            model_key
                        ]
                        merged = deep_merge_dict(existing, new_config)
                        self.model_registry.model_loaders_config["models"][
                            model_key
                        ] = merged
                    else:
                        self.model_registry.model_loaders_config["models"][
                            model_key
                        ] = new_config

    def _get_current_config(self, model_key: str) -> dict[str, Any]:
        """Get current configuration for rollback."""
        if model_key == "model_loaders":
            return self.model_registry.model_loaders_config.copy()
        if "models" in self.model_registry.model_loaders_config:
            return (
                self.model_registry.model_loaders_config["models"]
                .get(model_key, {})
                .copy()
            )
        return {}

    def _get_merged_config(self, model_key: str) -> dict[str, Any]:
        """Get merged configuration after update."""
        if model_key == "model_loaders":
            return self.model_registry.model_loaders_config
        if "models" in self.model_registry.model_loaders_config:
            return self.model_registry.model_loaders_config["models"].get(model_key, {})
        return {}

    async def _rollback_config(self, model_key: str, original_config: dict[str, Any]):
        """Rollback to previous configuration."""
        if model_key == "model_loaders":
            self.model_registry.model_loaders_config = original_config
        elif "models" in self.model_registry.model_loaders_config:
            self.model_registry.model_loaders_config["models"][model_key] = (
                original_config
            )
