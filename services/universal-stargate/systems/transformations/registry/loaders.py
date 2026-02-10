"""Configuration loaders for transformations - startup-only."""

from pathlib import Path
from typing import Any

import yaml
from model_id import ModelId
from universal_logging import get_logger

logger = get_logger(__name__)


class TransformationConfigLoader:
    """
    Loads transformation configs from YAML at startup.

    Invariant: All I/O happens in __init__, never during lookups.
    """

    def __init__(self, config_path: Path) -> None:
        """
        Load transformation configurations from YAML file.

        Args:
            config_path: Path to model_transformations.yaml

        Raises:
            FileNotFoundError: If config file doesn't exist
            yaml.YAMLError: If config file is invalid
        """
        self._configs: dict[str, dict[str, Any]] = {}
        self._basenames: list[str] = []

        if not config_path.exists():
            raise FileNotFoundError(f"Transformation config not found: {config_path}")

        with open(config_path) as f:
            data = yaml.safe_load(f)

        if not data or "transformations" not in data:
            logger.warning(f"No transformations in {config_path}")
            return

        for name, config in data["transformations"].items():
            if not config.get("enabled", True):
                logger.debug(f"Skipping disabled transformation: {name}")
                continue

            basename = config.get("basename")
            if basename is None:
                logger.debug(f"Skipping transformation without basename: {name}")
                continue

            basename_lower = basename.lower()
            self._basenames.append(basename_lower)
            self._configs[basename_lower] = {
                "name": name,
                "basename": basename,
                "description": config.get("description", ""),
                "settings": config.get("settings", {}),
            }

        # Sort basenames by length (descending) for longest-first matching
        self._basenames.sort(key=len, reverse=True)
        logger.info(f"Loaded {len(self._configs)} transformations from {config_path}")

    def get_for_model(self, model: ModelId) -> dict[str, Any] | None:
        """
        Get transformation config for a model using basename matching.

        Args:
            model: Parsed ModelId object

        Returns:
            Transformation config dict or None if no match
        """
        model_id_lower = model.base_id.lower()

        # Longest-first matching
        for basename in self._basenames:
            if model_id_lower == basename or model_id_lower.startswith(basename + "-"):
                config = self._configs[basename]
                logger.debug(f"Found transformation for {model}: {config['name']}")
                return config

        return None
