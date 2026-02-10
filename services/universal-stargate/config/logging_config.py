"""
Logging configuration module for Universal Stargate.

Provides centralized logging configuration with:
- Main application log (universal_stargate.log)
- Separate GUI log (gui.log)
- Error duplication (errors.log)
- DATA_DIR environment variable support for log directory location
"""

import os
from pathlib import Path

from universal_logging import get_logger


def get_log_directory() -> str:
    """
    Get the log directory path based on LOG_DIR or DATA_DIR environment variable.

    Priority:
        1. LOG_DIR (set by container entrypoint, e.g., /golem/logs/stargate)
        2. DATA_DIR/logs/universal-stargate (host mode fallback)
        3. /tmp/logs/universal-stargate (final fallback)

    Returns:
        str: Log directory path
    """
    # Check LOG_DIR first (set by container entrypoint)
    log_dir = os.environ.get("LOG_DIR")
    if log_dir:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        return log_dir

    # Fall back to DATA_DIR-based path
    data_dir = os.environ.get("DATA_DIR", "/tmp")
    log_dir = os.path.join(data_dir, "logs", "universal-stargate")
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    return log_dir


def load_logging_config(config_file: str = "config/logging.yaml"):
    """
    Load logging configuration from YAML file.

    Args:
        config_file: Path to logging configuration YAML file

    Returns:
        dict: Loaded configuration
    """
    from pathlib import Path

    import yaml

    config_path = Path(config_file)

    if not config_path.exists():
        # Fall back to universal_logging auto-initialization if config not found
        # First call to get_logger will trigger auto-initialization
        return {}

    try:
        # Load YAML configuration
        with open(config_path) as f:
            config = yaml.safe_load(f)

        # Get the log directory and update all file handler paths
        log_dir = get_log_directory()

        # Update file handler paths to use DATA_DIR-based log directory
        for handler_name, handler_config in config.get("handlers", {}).items():
            if handler_config.get("class") == "logging.FileHandler":
                # Get the original filename
                original_filename = handler_config.get("filename", "")
                # Check if it's a path with ${LOG_DIR:-logs} prefix or just starts with logs/
                if "${LOG_DIR" in original_filename or original_filename.startswith(
                    "logs/"
                ):
                    # Extract just the filename part (after last /)
                    filename = os.path.basename(
                        original_filename.split("}")[1]
                        if "}" in original_filename
                        else original_filename
                    )
                    handler_config["filename"] = os.path.join(log_dir, filename)

        # Expand environment variables in config (e.g., ${LOG_LEVEL:-INFO})
        from universal_logging.config_discovery import expand_env_vars

        config = expand_env_vars(config)

        # Uppercase log levels (Python logging requires uppercase)
        if "root" in config and "level" in config["root"]:
            config["root"]["level"] = str(config["root"]["level"]).upper()
        for handler_name, handler_config in config.get("handlers", {}).items():
            if isinstance(handler_config, dict) and "level" in handler_config:
                handler_config["level"] = str(handler_config["level"]).upper()
        for logger_name, logger_config in config.get("loggers", {}).items():
            if isinstance(logger_config, dict) and "level" in logger_config:
                logger_config["level"] = str(logger_config["level"]).upper()

        # Apply configuration with automatic truncation support
        from universal_logging import setup

        setup(config)

        return config

    except Exception as e:
        # Fall back to universal_logging auto-initialization on error
        # First call to get_logger will trigger auto-initialization
        import traceback

        print(f"ERROR: Failed to load logging config: {e}")
        traceback.print_exc()
        return {}


def get_domain_logger(domain: str = ""):
    """
    Get a domain-specific logger.

    Args:
        domain: Domain name ('gui', 'main', 'errors', etc.) or None for root logger

    Returns:
        Logger instance
    """
    if domain:
        return get_logger(domain)
    else:
        return get_logger("main")  # Use 'main' as the default domain


def setup_logging_for_component(component: str):
    """
    Setup logging for a specific component.

    Args:
        component: Component name ('gui', 'proxy', 'monitoring', etc.)
    """
    # Load the configuration
    load_logging_config()

    # Return appropriate logger
    return get_logger(component)
