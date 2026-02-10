"""
Configuration Validation

Validation and auto-fix for logging configuration.
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from universal_logging import INFO

if TYPE_CHECKING:
    from ..core import SmartLogger


def validate_configuration(smart_logger: "SmartLogger"):
    """Validate configuration thoroughly before applying.

    Args:
        smart_logger: SmartLogger instance with config
    """
    validation_errors = []

    # Validate formatters
    if "formatters" in smart_logger.config:
        for name, formatter_config in smart_logger.config["formatters"].items():
            try:
                if isinstance(formatter_config, dict):
                    # Skip validation for custom formatter classes
                    if "class" in formatter_config:
                        continue

                    test_formatter = logging.Formatter(
                        formatter_config.get("format", "%(message)s"),
                        formatter_config.get("datefmt"),
                    )
                    test_record = logging.LogRecord(
                        name="test",
                        level=INFO,
                        pathname="",
                        lineno=0,
                        msg="test message",
                        args=(),
                        exc_info=None,
                    )
                    test_formatter.format(test_record)
            except Exception as e:
                validation_errors.append(f"Invalid formatter {name}: {e}")
                fix_formatter(formatter_config)

    # Validate handlers
    if "handlers" in smart_logger.config:
        for name, handler_config in smart_logger.config["handlers"].items():
            try:
                validate_handler(name, handler_config)
            except Exception as e:
                validation_errors.append(f"Invalid handler {name}: {e}")
                fix_handler(smart_logger, name, handler_config)

    if validation_errors:
        smart_logger.metrics["warning_count"] += len(validation_errors)
        for error in validation_errors:
            logging.warning(f"Configuration validation: {error}")


def validate_handler(name: str, handler_config: dict[str, Any]):
    """Validate individual handler configuration.

    Args:
        name: Handler name
        handler_config: Handler configuration dict

    Raises:
        ValueError: If handler configuration is invalid
    """
    if not isinstance(handler_config, dict):
        raise ValueError(f"Handler {name} configuration must be a dictionary")

    if "stream" in handler_config and "ext://" in str(handler_config["stream"]):
        # Silent skip - external streams (like sys.stdout) are fine
        return

    if "class" not in handler_config:
        raise ValueError(f"Handler {name} missing required 'class' field")

    if handler_config.get("class") == "logging.FileHandler":
        if "filename" not in handler_config:
            raise ValueError(f"FileHandler {name} missing required 'filename' field")


def fix_formatter(formatter_config: dict[str, Any]):
    """Automatically fix common formatter configuration issues.

    Args:
        formatter_config: Formatter configuration dict (mutated in place)
    """
    if isinstance(formatter_config, dict):
        if "format" not in formatter_config or not formatter_config["format"]:
            formatter_config["format"] = (
                "[%(asctime)s] %(levelname)s [%(name)s] %(message)s"
            )

        if "datefmt" not in formatter_config:
            formatter_config["datefmt"] = "%Y-%m-%d %H:%M:%S"


def fix_handler(smart_logger: "SmartLogger", name: str, handler_config: dict[str, Any]):
    """Automatically fix common handler configuration issues.

    Args:
        smart_logger: SmartLogger instance
        name: Handler name
        handler_config: Handler configuration dict (mutated in place)
    """
    if isinstance(handler_config, dict):
        if handler_config.get("class") == "logging.FileHandler":
            if "filename" in handler_config:
                filename = Path(handler_config["filename"])
                if not filename.is_absolute():
                    handler_config["filename"] = str(Path.cwd() / filename)

        if "formatter" in handler_config:
            formatter_name = handler_config["formatter"]
            if formatter_name not in smart_logger.config.get("formatters", {}):
                handler_config["formatter"] = "standard"
