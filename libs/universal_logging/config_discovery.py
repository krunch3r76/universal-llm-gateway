"""
Configuration Discovery Module

Implements intelligent configuration file discovery from multiple
standard locations with priority-based merging.
"""

import os
from pathlib import Path
from typing import Any

import yaml


def discover_config_files(service_name: str, workspace_root: Path) -> list[Path]:
    """
    Search standard locations for logging configuration files.

    Search order (first found wins for single-file configs, or all merged):
    1. Current working directory: ./logging.yaml
    2. Current config subdirectory: ./config/logging.yaml
    3. Service-specific in workspace:
       {workspace}/services/{service}/config/logging.yaml
    4. Workspace config directory: {workspace}/config/logging.yaml
    5. Libs universal_logging config:
       {workspace}/libs/universal_logging/config/default.yaml
    6. User config directory: ~/.config/{service}/logging.yaml
    7. System config: /etc/{service}/logging.yaml

    Args:
        service_name: Name of the service
        workspace_root: Path to workspace root directory

    Returns:
        List of existing configuration file paths
    """
    search_paths = []

    # 1. Universal logging default config (Base)
    search_paths.append(
        workspace_root / "libs" / "universal_logging" / "config" / "default.yaml"
    )

    # 2. System-wide config
    system_config = Path("/etc") / service_name / "logging.yaml"
    search_paths.append(system_config)

    # 3. Workspace-level config
    search_paths.append(workspace_root / "config" / "logging.yaml")

    # 4. Service-specific config in workspace
    service_config = (
        workspace_root / "services" / service_name / "config" / "logging.yaml"
    )
    search_paths.append(service_config)

    # 5. User config directory
    user_config_dir = Path.home() / ".config" / service_name
    search_paths.append(user_config_dir / "logging.yaml")

    # 6. Current working directory (Highest Priority)
    search_paths.append(Path.cwd() / "config" / "logging.yaml")
    search_paths.append(Path.cwd() / "logging.yaml")

    # Return only existing files
    existing_files = [p for p in search_paths if p.exists() and p.is_file()]

    return existing_files


def load_yaml_config(config_path: Path) -> dict[str, Any] | None:
    """
    Load YAML configuration file.

    Args:
        config_path: Path to YAML config file

    Returns:
        Configuration dict or None if loading fails
    """
    try:
        with open(config_path) as f:
            config = yaml.safe_load(f)
            return config if config else {}
    except Exception as e:
        # Policy: caught exceptions must log WARN/ERROR or re-raise
        import logging
        logging.getLogger(__name__).warning(
            f"Failed to load config file {config_path}: {e}"
        )
        return None


def merge_configs(
    base_config: dict[str, Any], override_config: dict[str, Any]
) -> dict[str, Any]:
    """
    Merge two configuration dictionaries, with override taking precedence.

    Args:
        base_config: Base configuration
        override_config: Override configuration (takes precedence)

    Returns:
        Merged configuration
    """
    result = base_config.copy()

    for key, value in override_config.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            # Recursively merge nested dicts
            result[key] = merge_configs(result[key], value)
        else:
            # Override value
            result[key] = value

    return result


def load_configuration(
    service_name: str, workspace_root: Path
) -> dict[str, Any] | None:
    """
    Load and merge configuration from discovered files.

    Discovers all available configuration files and merges them with
    appropriate precedence (later configs override earlier ones).

    Args:
        service_name: Name of the service
        workspace_root: Path to workspace root directory

    Returns:
        Merged configuration dict or None if no configs found
    """
    config_files = discover_config_files(service_name, workspace_root)

    if not config_files:
        return None

    # Load and merge all configs (later ones override earlier ones)
    merged_config = {}

    for config_path in config_files:
        config = load_yaml_config(config_path)
        if config:
            merged_config = merge_configs(merged_config, config)

    return merged_config if merged_config else None


def expand_env_vars(config: dict[str, Any]) -> dict[str, Any]:
    """
    Recursively expand environment variables in configuration.

    Supports ${VAR} and ${VAR:-default} syntax.

    Args:
        config: Configuration dict

    Returns:
        Configuration with expanded variables
    """
    import re

    def expand_value(value):
        if isinstance(value, str):
            # Pattern: ${VAR} or ${VAR:-default}
            pattern = r"\$\{([^}:]+)(?::(-?)([^}]*))?\}"

            def replacer(match):
                var_name = match.group(1)
                has_default = match.group(2) is not None
                default_value = match.group(3) if has_default else ""

                env_value = os.getenv(var_name)
                if env_value is not None:
                    return env_value
                elif has_default:
                    return default_value
                else:
                    return match.group(0)  # Keep original if no default

            return re.sub(pattern, replacer, value)
        elif isinstance(value, dict):
            return {k: expand_value(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [expand_value(item) for item in value]
        else:
            return value

    return expand_value(config)
