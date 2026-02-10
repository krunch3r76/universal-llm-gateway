"""Configuration loader for profiles - startup-only I/O."""

from pathlib import Path
from typing import Any

import yaml
from universal_hot_reload import read_text_preserving_timestamps
from universal_logging import get_logger

logger = get_logger(__name__)


class ProfileConfigLoader:
    """
    Loads profile definitions from YAML at startup.

    Invariant: All I/O happens in __init__, never during lookups.
    """

    def __init__(self, config_path: Path) -> None:
        """
        Load profile configurations from YAML file.

        Args:
            config_path: Path to profiles.yaml

        Raises:
            FileNotFoundError: If config file doesn't exist
            yaml.YAMLError: If config file is invalid
        """
        self._profiles: dict[str, dict[str, Any]] = {}
        self._config_path = config_path

        if not config_path.exists():
            raise FileNotFoundError(f"Profiles config not found: {config_path}")

        self._load_profiles()
        logger.info(f"Loaded {len(self._profiles)} profiles from {config_path}")

    def _load_profiles(self) -> None:
        """Load profiles from YAML file."""
        # Read without triggering editor change notifications
        content = read_text_preserving_timestamps(self._config_path)
        config = yaml.safe_load(content)

        if config is None:
            logger.warning(f"YAML file is empty: {self._config_path}")
            return

        self._profiles = config.get("profiles", {})

    def reload(self) -> int:
        """
        Reload profiles from disk (for hot-reload support).

        Returns:
            Number of profiles loaded

        Raises:
            Exception: If reload fails (fail-fast, no silent continue)
        """
        old_count = len(self._profiles)
        # Fail-fast: let exceptions propagate
        self._load_profiles()
        new_count = len(self._profiles)
        logger.info(f"🔄 Reloaded profiles: {old_count} -> {new_count}")
        return new_count

    @property
    def config_path(self) -> Path:
        """Get config path (for hot-reload watcher)."""
        return self._config_path

    def get(self, name: str) -> dict[str, Any] | None:
        """Get profile definition by name."""
        return self._profiles.get(name)

    def exists(self, name: str) -> bool:
        """Check if profile exists."""
        return name in self._profiles

    def list_profiles(self) -> list[str]:
        """List all profile names."""
        return list(self._profiles.keys())

    def get_all(self) -> dict[str, dict[str, Any]]:
        """Get all profile definitions (copy)."""
        return self._profiles.copy()
