"""
Handler Setup

Handler configuration orchestration and directory management.
"""

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from universal_logging.bootstrap import bootstrap_logger

if TYPE_CHECKING:
    from ..core import SmartLogger


def setup_handlers(smart_logger: "SmartLogger"):
    """Configure logging handlers with comprehensive error handling.

    Args:
        smart_logger: SmartLogger instance to configure
    """
    from ..health import setup_emergency_fallback
    from .config_apply import apply_logging_config_with_cleanup
    from .validation import validate_configuration

    bootstrap_logger.debug("Setting up handlers...")

    try:
        ensure_log_directories(smart_logger)
        validate_configuration(smart_logger)

        try:
            apply_logging_config_with_cleanup(smart_logger.config)
        except Exception as dict_config_error:
            import traceback

            logging.error(f"dictConfig failed: {dict_config_error}")
            logging.error(f"Traceback: {traceback.format_exc()}")
            raise

        bootstrap_logger.debug("Handler setup complete")

    except Exception as e:
        smart_logger.metrics["error_count"] += 1
        smart_logger._last_error = str(e)
        setup_emergency_fallback(smart_logger, e)


def ensure_log_directories(smart_logger: "SmartLogger"):
    """Ensure all log directories exist with proper permissions.

    Args:
        smart_logger: SmartLogger instance with config
    """
    if "handlers" not in smart_logger.config:
        return

    handlers_to_process = list(smart_logger.config["handlers"].items())
    failed_handlers = []

    for handler_name, handler_config in handlers_to_process:
        if isinstance(handler_config, dict) and "filename" in handler_config:
            try:
                log_file = Path(handler_config["filename"])
                log_dir = log_file.parent

                log_dir.mkdir(parents=True, exist_ok=True, mode=0o755)

                # Verify write permissions
                test_file = log_dir / f".test_write_{os.getpid()}"
                test_file.touch()
                test_file.unlink()

            except Exception as e:
                logging.warning(
                    f"Failed to setup log directory for {handler_name}: {e}"
                )
                failed_handlers.append(handler_name)

    for handler_name in failed_handlers:
        remove_file_handler(smart_logger, handler_name)


def remove_file_handler(smart_logger: "SmartLogger", handler_name: str):
    """Remove problematic file handler from configuration.

    Args:
        smart_logger: SmartLogger instance
        handler_name: Name of handler to remove
    """
    if (
        "handlers" in smart_logger.config
        and handler_name in smart_logger.config["handlers"]
    ):
        del smart_logger.config["handlers"][handler_name]
        smart_logger.metrics["failed_handlers"].append(handler_name)

        # Remove from loggers
        if "loggers" in smart_logger.config:
            for logger_config in smart_logger.config["loggers"].values():
                if isinstance(logger_config, dict) and "handlers" in logger_config:
                    if isinstance(logger_config["handlers"], list):
                        if handler_name in logger_config["handlers"]:
                            logger_config["handlers"].remove(handler_name)

        # Remove from root logger (prevents "Unable to configure handler" error)
        if "root" in smart_logger.config:
            root_config = smart_logger.config["root"]
            if isinstance(root_config, dict) and "handlers" in root_config:
                if isinstance(root_config["handlers"], list):
                    if handler_name in root_config["handlers"]:
                        root_config["handlers"].remove(handler_name)
