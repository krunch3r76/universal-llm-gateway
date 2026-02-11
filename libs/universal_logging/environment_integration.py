"""
Environment Integration Module

Handles environment variable integration for logging configuration,
providing overrides and fallbacks for runtime configuration.
"""

import os
from pathlib import Path
from typing import Any


def get_log_level() -> str:
    """
    Get log level from environment or default.

    Returns:
        Log level string (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    level = os.getenv("LOG_LEVEL", "INFO").upper()

    # Validate level
    valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    if level not in valid_levels:
        return "INFO"

    return level


def get_log_file() -> Path | None:
    """
    Get log file path from environment.

    This sets the full path for the handler named "file" in the logging configuration.
    Takes precedence over LOG_DIR for the "file" handler.

    Environment Variable:
        LOG_FILE: Full path to main log file (e.g., "/var/log/myapp/app.log")

    Note:
        - Only affects handlers named "file" in the configuration
        - When both LOG_FILE and LOG_DIR are set, LOG_FILE takes precedence
        - Other file handlers (e.g., "error_file") are still affected by LOG_DIR

    Returns:
        Path to log file or None if not specified
    """
    log_file = os.getenv("LOG_FILE")
    if log_file:
        return Path(log_file)
    return None


def get_log_dir() -> Path | None:
    """
    Get log directory path from environment.

    This sets the directory for all file handlers by extracting the filename
    from each handler's current path and reconstructing it with LOG_DIR.

    Environment Variable:
        LOG_DIR: Directory for all log files (e.g., "/var/log")

    Precedence:
        - LOG_FILE takes precedence for the "file" handler
        - LOG_DIR applies to all other file handlers (error_file, audit_file, etc.)

    Example:
        LOG_DIR=/var/log with handler filename="errors.log" → "/var/log/errors.log"

    Returns:
        Path to log directory or None if not specified
    """
    log_dir = os.getenv("LOG_DIR")
    if log_dir:
        return Path(log_dir)
    return None


def get_use_colors() -> bool:
    """
    Check if colored output is enabled.

    Returns:
        True if colors should be used, False otherwise
    """
    use_colors = os.getenv("USE_COLORS", "true").lower()
    return use_colors in ["true", "1", "yes", "on"]


def get_use_effects() -> bool:
    """
    Check if terminal effects (bold, etc.) are enabled.

    Returns:
        True if effects should be used, False otherwise
    """
    use_effects = os.getenv("USE_EFFECTS", "true").lower()
    return use_effects in ["true", "1", "yes", "on"]


def get_truncate_logs() -> bool:
    """
    Check if log truncation is enabled.

    Returns:
        True if logs should be truncated, False otherwise
    """
    truncate = os.getenv("TRUNCATE_LOGS", "false").lower()
    return truncate in ["true", "1", "yes", "on"]


def apply_environment_overrides(config: dict[str, Any]) -> dict[str, Any]:
    """
    Apply environment variable overrides to configuration.

    Environment Variable Precedence (high to low):
        1. LOG_FILE - Sets full path for "file" handler specifically
        2. LOG_DIR - Sets directory for all file handlers except those set by LOG_FILE
        3. LOG_LEVEL - Sets log level for root logger and console/file handlers
        4. USE_COLORS, USE_EFFECTS - Control formatter appearance
        5. TRUNCATE_LOGS - Control log message truncation

    Interaction between LOG_FILE and LOG_DIR:
        - If LOG_FILE is set, it completely controls the "file" handler path
        - If LOG_DIR is set, it only affects file handlers NOT already set by LOG_FILE
        - This ensures the more specific LOG_FILE setting takes precedence

    Example Scenarios:
        1. LOG_FILE="/custom/app.log", LOG_DIR="/logs"
           → file handler: "/custom/app.log"
           → error_file handler: "/logs/errors.log"

        2. LOG_DIR="/var/log" only
           → file handler: "/var/log/app.log"
           → error_file handler: "/var/log/errors.log"

        3. LOG_FILE="/tmp/test.log" only
           → file handler: "/tmp/test.log"
           → error_file handler: "errors.log" (unchanged)

    Args:
        config: Base configuration dict

    Returns:
        Configuration dict with environment overrides applied
    """
    # Create a copy to avoid modifying original
    config = config.copy()

    # Override log level
    log_level = get_log_level()
    if "root" in config:
        config["root"]["level"] = log_level

    # Override log file if specified (most specific - takes precedence)
    # LOG_FILE only affects the handler named "file"
    log_file = get_log_file()
    handlers_set_by_log_file = set()
    if log_file and "handlers" in config:
        if "file" in config["handlers"]:
            config["handlers"]["file"]["filename"] = str(log_file)
            handlers_set_by_log_file.add("file")
            # Track that this handler was set by LOG_FILE to protect it from LOG_DIR

    # Override log directory for file handlers (only if not already set by LOG_FILE)
    # LOG_DIR affects all file handlers except those protected by LOG_FILE
    log_dir = get_log_dir()
    if log_dir and "handlers" in config:
        for handler_name, handler_config in config["handlers"].items():
            if (
                "filename" in handler_config
                and handler_name not in handlers_set_by_log_file
            ):
                # Extract just the filename and reconstruct with LOG_DIR
                filename = Path(handler_config["filename"]).name
                handler_config["filename"] = str(log_dir / filename)

    # Override color settings
    if "formatters" in config:
        use_colors = get_use_colors()
        use_effects = get_use_effects()

        for formatter_name, formatter_config in config["formatters"].items():
            if isinstance(formatter_config, dict):
                if "use_colors" in formatter_config:
                    formatter_config["use_colors"] = use_colors
                if "use_effects" in formatter_config:
                    formatter_config["use_effects"] = use_effects

    # Override truncate setting
    if "logging" in config:
        config["logging"]["truncate_logs"] = get_truncate_logs()

    return config


def create_minimal_config(service_name: str) -> dict[str, Any]:
    """
    Create minimal logging configuration from environment variables only.

    This is used as a fallback when no YAML configuration files are found.

    Args:
        service_name: Name of the service

    Returns:
        Minimal configuration dict
    """
    log_level = get_log_level()
    log_file = get_log_file()
    log_dir = get_log_dir()

    config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {
                "format": "[%(asctime)s] %(levelname)s [%(name)s] %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            }
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "level": log_level,
                "formatter": "standard",
                "stream": "ext://sys.stderr",
            }
        },
        "root": {"level": log_level, "handlers": ["console"]},
    }

    # Add file handler if log file or directory specified
    if log_file or log_dir:
        if log_file:
            filename = log_file
        elif log_dir:
            filename = log_dir / f"{service_name}.log"
        else:
            filename = Path(f"{service_name}.log")

        config["handlers"]["file"] = {
            "class": "logging.FileHandler",
            "level": log_level,
            "formatter": "standard",
            "filename": str(filename),
            "mode": "a",
        }
        config["root"]["handlers"].append("file")

    return config
