"""
Worker logging setup - minimal module without auto-initialization triggers.

This module MUST NOT import universal_logging.get_logger() at module level
to prevent SmartLogger auto-initialization from discovering logging.yaml
with truncate_logs: true, which would truncate the gateway log file.

The setup_worker_logging() function is imported by worker/__main__.py
BEFORE any other imports that use universal_logging.
"""

import logging
from pathlib import Path


def setup_worker_logging(level: str = "INFO", log_file: str | None = None) -> None:
    """
    Setup logging for worker processes.

    MUST be called before any get_logger() calls to prevent SmartLogger
    auto-initialization from loading logging.yaml with truncate_logs: true.

    Prevents handler accumulation by closing existing FileHandlers before
    applying new configuration. This ensures each worker has exactly one
    handler per log destination.

    Args:
        level: Logging level
        log_file: Optional log file path
    """
    # Close and remove existing FileHandlers to prevent accumulation
    # This is critical when setup_worker_logging() is called multiple times
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        if isinstance(handler, logging.FileHandler):
            handler.close()
            root_logger.removeHandler(handler)

    logging_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "()": "universal_logging.JSONFormatter",
            }
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "level": level,
                "formatter": "default",
                "stream": "ext://sys.stdout",
            }
        },
        "root": {"level": level, "handlers": ["console"]},
    }

    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        logging_config["handlers"]["file"] = {
            "class": "logging.FileHandler",
            "level": level,
            "formatter": "default",
            "filename": str(log_path),
        }
        logging_config["root"]["handlers"].append("file")

    # Apply configuration - this import happens INSIDE the function
    # to avoid triggering SmartLogger auto-init at module load time
    from universal_logging import setup

    setup(logging_config)
