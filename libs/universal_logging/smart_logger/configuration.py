"""
SmartLogger Configuration

Configuration loading, normalization, and sanitization.
"""

import logging
from typing import Any


def load_and_prepare_configuration(
    service_name: str | None, workspace_root
) -> dict[str, Any]:
    """Load configuration with safe fallbacks.

    Args:
        service_name: Detected service name
        workspace_root: Detected workspace root path

    Returns:
        Prepared logging configuration dict
    """
    try:
        from ..config_discovery import expand_env_vars, load_configuration
        from ..environment_integration import (
            apply_environment_overrides,
            create_minimal_config,
        )

        config = load_configuration(service_name, workspace_root)

        if config is None:
            config = create_minimal_config(service_name)

        config = expand_env_vars(config)
        config = apply_environment_overrides(config)

        return config

    except Exception as e:
        logging.warning(f"Configuration loading failed: {e}")
        from ..environment_integration import create_minimal_config

        return create_minimal_config(service_name or "universal")


def normalize_log_levels(config: dict[str, Any]) -> dict[str, Any]:
    """Normalize all log level strings to uppercase (required by Python logging).

    Args:
        config: Logging configuration dict

    Returns:
        Config with normalized log levels
    """
    # Normalize root logger level
    if "root" in config and isinstance(config["root"], dict):
        if "level" in config["root"] and isinstance(config["root"]["level"], str):
            config["root"]["level"] = config["root"]["level"].upper()

    # Normalize handler levels
    if "handlers" in config:
        for handler_config in config["handlers"].values():
            if isinstance(handler_config, dict) and "level" in handler_config:
                if isinstance(handler_config["level"], str):
                    handler_config["level"] = handler_config["level"].upper()

    # Normalize logger levels
    if "loggers" in config:
        for logger_config in config["loggers"].values():
            if isinstance(logger_config, dict) and "level" in logger_config:
                if isinstance(logger_config["level"], str):
                    logger_config["level"] = logger_config["level"].upper()

    return config


def sanitize_formatters(config: dict[str, Any]) -> dict[str, Any]:
    """Replace self-referencing formatters with safe alternatives.

    Args:
        config: Logging configuration dict

    Returns:
        Config with sanitized formatters
    """
    if "formatters" not in config:
        return config

    safe_formatters = {}
    circular_refs = _detect_circular_references(config)

    if circular_refs:
        logging.warning(
            f"Detected circular references in configuration: {circular_refs}"
        )

    for name, formatter_config in config["formatters"].items():
        if isinstance(formatter_config, dict):
            safe_formatter = formatter_config.copy()

            # Only sanitize callable formatter factories (with "()")
            # Normal formatter classes are safe to use
            if "()" in str(safe_formatter):
                logging.warning(
                    f"Sanitizing callable factory in formatter '{name}': "
                    f"{safe_formatter}"
                )
                safe_formatter = {
                    "format": safe_formatter.get(
                        "format",
                        "[%(asctime)s] %(levelname)s [%(name)s] %(message)s",
                    ),
                    "datefmt": safe_formatter.get("datefmt", "%Y-%m-%d %H:%M:%S"),
                }

            safe_formatters[name] = safe_formatter
        else:
            safe_formatters[name] = formatter_config

    config["formatters"] = safe_formatters
    return config


def _detect_circular_references(config: dict[str, Any]) -> list[str]:
    """Detect circular references in configuration.

    Args:
        config: Logging configuration dict

    Returns:
        List of detected circular reference descriptions
    """
    circular_refs = []

    if "formatters" in config:
        for name, formatter_config in config["formatters"].items():
            if isinstance(formatter_config, dict):
                # Only flag callable factories as circular references
                if "()" in str(formatter_config):
                    circular_refs.append(f"formatter.{name}: contains callable factory")

    return circular_refs
