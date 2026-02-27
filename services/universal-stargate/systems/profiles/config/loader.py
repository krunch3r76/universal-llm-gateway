"""Configuration loader for profiles - startup-only I/O."""

from pathlib import Path
from typing import Any

import yaml
from universal_hot_reload import read_text_preserving_timestamps
from universal_logging import get_logger

logger = get_logger(__name__)

# Repo-bundled profiles are the base set; config_path (e.g. ~/.gateway/profiles.yaml)
# adds or overrides individual entries by name.
_REPO_PROFILES_PATH: Path = Path(__file__).parents[3] / "config" / "profiles.yaml"


class ProfileConfigLoader:
    """
    Loads profile definitions from YAML at startup.

    Sources merged in order (later entries override earlier ones by name):
      1. Repo base: services/universal-stargate/config/profiles.yaml
      2. User override: config_path (e.g. ~/.gateway/profiles.yaml, optional)

    Invariant: All I/O happens in __init__, never during lookups.
    """

    def __init__(self, config_path: Path) -> None:
        """
        Load profile configurations, merging repo base with user override.

        Args:
            config_path: Path to user profiles.yaml (e.g. ~/.gateway/profiles.yaml).
                         Need not exist — repo base is always loaded as fallback.
        """
        self._profiles: dict[str, dict[str, Any]] = {}
        self._config_path = config_path

        self._load_profiles()
        logger.info(f"Loaded {len(self._profiles)} profiles (repo + {config_path})")

    def _load_profiles(self) -> None:
        """Load profiles: repo as base, config_path as per-name override."""
        merged: dict[str, dict[str, Any]] = {}

        if _REPO_PROFILES_PATH.exists():
            content = read_text_preserving_timestamps(_REPO_PROFILES_PATH)
            repo_config = yaml.safe_load(content)
            if repo_config:
                merged.update(repo_config.get("profiles", {}))
        else:
            logger.warning(f"Repo profiles base not found: {_REPO_PROFILES_PATH}")

        # Only overlay user config when it differs from repo path (avoids double-load)
        if self._config_path.resolve() != _REPO_PROFILES_PATH.resolve():
            if self._config_path.exists():
                content = read_text_preserving_timestamps(self._config_path)
                user_config = yaml.safe_load(content)
                if user_config:
                    merged.update(user_config.get("profiles", {}))
            else:
                logger.debug(
                    f"User profiles not found: {self._config_path},"
                    " using repo base only"
                )

        if not merged:
            logger.warning("No profiles loaded from repo or user config")

        self._profiles = merged

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
