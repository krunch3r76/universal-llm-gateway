"""Configuration loaders for transformations - startup-only."""

from pathlib import Path
from typing import Any

import yaml
from model_id import ModelId
from universal_logging import get_logger

logger = get_logger(__name__)

# Repo-bundled transformations are the base set; config_path (e.g.
# ~/.gateway/model_transformations.yaml) adds or overrides entries by name.
# TODO: In a future iteration, the catalog should also be consulted as a source
#       for transformation definitions (per-model metadata stored centrally).
_REPO_TRANSFORMATIONS_PATH: Path = (
    Path(__file__).parents[3] / "config" / "model_transformations.yaml"
)


class TransformationConfigLoader:
    """
    Loads transformation configs from YAML at startup.

    Sources merged in order (later entries override earlier ones by name):
      1. Repo base: services/universal-stargate/config/model_transformations.yaml
      2. User override: config_path (e.g. ~/.gateway/model_transformations.yaml)

    Invariant: All I/O happens in __init__, never during lookups.
    """

    def __init__(self, config_path: Path) -> None:
        """
        Load transformation configurations, merging repo base with user override.

        Args:
            config_path: Path to user model_transformations.yaml (e.g.
                         ~/.gateway/model_transformations.yaml). Need not exist —
                         repo base is always loaded as fallback.
        """
        self._configs: dict[str, dict[str, Any]] = {}
        self._basenames: list[str] = []

        raw: dict[str, Any] = {}
        _load_raw_transformations_into(raw, _REPO_TRANSFORMATIONS_PATH, label="repo")
        if config_path.resolve() != _REPO_TRANSFORMATIONS_PATH.resolve():
            _load_raw_transformations_into(raw, config_path, label="user")

        _build_transformation_index(raw, self._configs, self._basenames)
        logger.info(
            f"Loaded {len(self._configs)} transformations (repo + {config_path})"
        )

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


def _load_raw_transformations_into(
    dest: dict[str, Any], path: Path, *, label: str
) -> None:
    """Merge raw transformation entries from a YAML file into dest (keyed by name)."""
    if not path.exists():
        logger.debug(f"Transformation config not found ({label}): {path}")
        return

    with open(path) as f:
        data = yaml.safe_load(f)

    if not data or "transformations" not in data:
        logger.warning(f"No transformations in {path} ({label})")
        return

    dest.update(data["transformations"])


def _build_transformation_index(
    raw: dict[str, Any],
    configs: dict[str, dict[str, Any]],
    basenames: list[str],
) -> None:
    """Populate configs and basenames from merged raw transformation entries."""
    configs.clear()
    basenames.clear()

    for name, config in raw.items():
        if not config.get("enabled", True):
            logger.debug(f"Skipping disabled transformation: {name}")
            continue

        basename = config.get("basename")
        if basename is None:
            logger.debug(f"Skipping transformation without basename: {name}")
            continue

        basename_lower = basename.lower()
        basenames.append(basename_lower)
        configs[basename_lower] = {
            "name": name,
            "basename": basename,
            "description": config.get("description", ""),
            "settings": config.get("settings", {}),
        }

    # Sort basenames by length (descending) for longest-first matching
    basenames.sort(key=len, reverse=True)
