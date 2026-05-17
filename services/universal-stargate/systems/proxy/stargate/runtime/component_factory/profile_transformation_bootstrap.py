"""Profile and transformation bootstrap for Stargate component initialization.

This module centralizes resolution of the Stargate configuration directory and
owns the creation of TransformationEngine and ProfileManager. These are pure
startup I/O operations that read YAML config files from disk.

It is intentionally free of intra-package dependencies so it can be imported
early during the component_factory package construction. Other modules
(request_component_bootstrap, intelligence_profile_bootstrap) import its
_get_config_dir helper and the two initialize_* functions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from universal_logging import get_logger

from .....profiles import ProfileConfigLoader, ProfileManager
from .....transformations import TransformationConfigLoader, TransformationEngine

logger = get_logger(__name__)


def _get_config_dir(config: Any) -> Path:
    """Resolve the configuration directory from a config object's config_path.

    Falls back to a local "config" directory when no config_path is present
    (e.g., during unit tests or in-memory configurations).

    Args:
        config: Stargate configuration object exposing an optional ``config_path``
            attribute (usually a pathlib.Path or str pointing at the YAML file).

    Returns:
        Absolute or relative Path to the directory containing stargate_config.yaml
        and related files (profiles.yaml, model_transformations.yaml, etc.).
    """
    return Path(config.config_path).parent if config.config_path else Path("config")


def initialize_transformation_engine(config_dir: Path) -> TransformationEngine:
    """
    Initialize TransformationEngine at startup.

    Args:
        config_dir: Path to config directory containing model_transformations.yaml

    Returns:
        Configured TransformationEngine
    """
    config_path = config_dir / "model_transformations.yaml"
    try:
        config_loader = TransformationConfigLoader(config_path)
        engine = TransformationEngine(config_loader=config_loader)
        logger.info("TransformationEngine initialized")
        return engine
    except FileNotFoundError:
        logger.warning(f"No transformation config at {config_path}, using empty config")

        # Create empty loader for no-config case
        class EmptyConfigLoader:
            """Fallback no-op loader used when model_transformations.yaml is absent.

            TransformationEngine requires a loader that implements get_for_model(model)
            returning either a transformation spec or None. This class guarantees
            the latter for every model, preserving existing "no transformations"
            behavior without requiring a config file on disk.
            """

            def get_for_model(self, model):
                return None

        return TransformationEngine(config_loader=EmptyConfigLoader())


def initialize_profile_manager(config_dir: Path) -> ProfileManager:
    """
    Initialize ProfileManager at startup.

    Profiles are merged from the repo base and config_dir override: the repo
    config is always loaded as the base; config_dir/profiles.yaml overrides
    individual entries by name when present.

    Args:
        config_dir: Path to config directory (e.g. ~/.gateway)

    Returns:
        Configured ProfileManager
    """
    config_path = config_dir / "profiles.yaml"
    config_loader = ProfileConfigLoader(config_path)
    manager = ProfileManager(config_loader=config_loader)
    logger.info("ProfileManager initialized")
    return manager
