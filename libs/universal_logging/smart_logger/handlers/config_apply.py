"""
Configuration Application

Logging configuration application with automatic handler cleanup and replacement.
"""

import copy
import logging.config
from typing import Any

from universal_logging.bootstrap import bootstrap_logger


def apply_logging_config_with_cleanup(config: dict[str, Any]) -> None:
    """Apply logging configuration with automatic truncation support.

    Integrates automatic handler cleanup when truncate_logs: true is detected.
    This eliminates the need for manual service coordination.

    Also automatically replaces logging.FileHandler with AutoFlushFileHandler
    to ensure logs are written immediately to disk.

    Args:
        config: Logging configuration dict (may be modified by removing truncate_logs)
    """
    # Make a deep copy to avoid mutating the original config
    config_copy = copy.deepcopy(config)

    # Extract truncate_logs setting (top-level or legacy nested under "logging")
    truncate_logs = config_copy.pop("truncate_logs", False)
    nested_logging = config_copy.pop("logging", None)
    if not truncate_logs and isinstance(nested_logging, dict):
        truncate_logs = nested_logging.pop("truncate_logs", False)

    # Automatic handler cleanup and mode setting if truncation requested
    if truncate_logs:
        bootstrap_logger.debug("Truncate logs enabled, closing existing handlers")
        handlers_closed = close_existing_handlers()
        bootstrap_logger.debug(f"Closed {handlers_closed} file handlers")

        # Set mode='w' for all FileHandlers to ensure truncation
        set_truncation_mode(config_copy)

    # Replace logging.FileHandler with AutoFlushFileHandler for immediate writes
    replace_file_handlers(config_copy)

    # Apply configuration (creates new handlers if truncate_logs was true)
    logging.config.dictConfig(config_copy)


def close_existing_handlers() -> int:
    """
    Close all existing file handlers for truncation.

    Returns:
        Number of handlers closed
    """
    from universal_logging.handler_cleanup import close_all_file_handlers

    return close_all_file_handlers()


def set_truncation_mode(config: dict) -> None:
    """
    Set mode='w' for all FileHandlers to ensure truncation.

    Args:
        config: Logging configuration dict (mutated in place)
    """
    if "handlers" not in config:
        return

    for handler_config in config["handlers"].values():
        if isinstance(handler_config, dict):
            handler_class = handler_config.get("class", "")
            if "FileHandler" in handler_class:
                handler_config["mode"] = "w"


def replace_file_handlers(config: dict) -> None:
    """
    Replace logging.FileHandler with AutoFlushFileHandler in config.

    Args:
        config: Logging configuration dict (mutated in place)
    """
    if "handlers" not in config:
        return

    for handler_config in config["handlers"].values():
        if isinstance(handler_config, dict):
            handler_class = handler_config.get("class", "")
            # Replace standard FileHandler with auto-flushing version
            if handler_class == "logging.FileHandler":
                handler_config["class"] = (
                    "universal_logging.handlers.AutoFlushFileHandler"
                )
            # Keep RotatingFileHandler as-is (already a subclass)
            elif handler_class == "logging.handlers.RotatingFileHandler":
                pass
