"""
Engine Environment Variable Loader

DEPRECATED: engine_env.yaml has been removed. Engine environment variables
are now set via docker/compose/engine-optimizations.env (loaded by Docker Compose).

This module remains for backward compatibility but no longer loads any configuration.
The load_engine_env() function returns an empty dict.
"""

import os
from pathlib import Path

import yaml


def load_engine_env(config_path: Path | None = None) -> dict[str, str]:
    """
    Load and apply engine environment variables from config file.

    Args:
        config_path: Path to engine_env.yaml. If None, uses default location.

    Returns:
        Dict of applied environment variables and their values.
    """
    if config_path is None:
        config_path = Path(__file__).parent / "engine_env.yaml"

    if not config_path.exists():
        return {}

    with open(config_path) as f:
        config = yaml.safe_load(f)

    if not config:
        return {}

    applied = {}

    for var_name, settings in config.items():
        if not isinstance(settings, dict):
            continue

        value = settings.get("value")
        mode = settings.get("mode", "setdefault")

        if mode == "set":
            # Always set, overwriting existing
            if value is not None:
                os.environ[var_name] = str(value)
                applied[var_name] = str(value)

        elif mode == "setdefault":
            # Only set if not already defined
            if value is not None:
                os.environ.setdefault(var_name, str(value))
                applied[var_name] = os.environ.get(var_name, str(value))

        elif mode == "unset":
            # Remove if exists
            if var_name in os.environ:
                del os.environ[var_name]
                applied[var_name] = "(unset)"

    return applied


# DEPRECATED: Auto-load removed. Engine vars now set via docker/compose/engine-optimizations.env
# Keeping empty dict for backward compatibility
_applied_env = {}
