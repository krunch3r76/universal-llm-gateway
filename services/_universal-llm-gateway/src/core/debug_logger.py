"""Dedicated debug logger for streaming diagnostics.

Provides a separate log file for STREAM_DEBUG messages to avoid noise
in the main application logs. Configured to write to current_task_debug.log
with timestamp precision for correlation analysis.
"""

import logging
import os
from pathlib import Path

from universal_logging import INFO, get_logger

# Create dedicated debug logger
debug_logger = get_logger("stream_debug")
debug_logger.setLevel(INFO)

# Avoid duplicate logs if logger already configured
if not debug_logger.handlers:
    # Use LOG_DIR from environment (set by main.py before imports)
    log_dir_str = os.getenv("LOG_DIR")
    if not log_dir_str:
        # Fallback (should never happen if main.py setup worked)
        data_dir = os.getenv("DATA_DIR", "/tmp")
        log_dir_str = os.path.join(data_dir, "logs", "universal-llm-gateway")

    log_dir = Path(log_dir_str)
    log_dir.mkdir(parents=True, exist_ok=True)

    # Create file handler for debug log
    debug_file = log_dir / "current_task_debug.log"
    file_handler = logging.FileHandler(debug_file, mode="w")  # Overwrite on each run

    # Create simple formatter with high precision timestamps
    formatter = logging.Formatter(
        fmt="%(asctime)s.%(msecs)03d - %(message)s", datefmt="%H:%M:%S"
    )
    file_handler.setFormatter(formatter)

    # Add handler to logger
    debug_logger.addHandler(file_handler)

    # Prevent propagation to root logger to avoid duplicate messages
    debug_logger.propagate = False


def log_stream_debug(message: str) -> None:
    """Log a stream debug message to the dedicated debug log.

    Args:
        message: Debug message to log
    """
    debug_logger.info(message)


def clear_debug_log() -> None:
    """Clear the debug log file for a fresh start."""
    try:
        # Use LOG_DIR from environment
        log_dir_str = os.getenv("LOG_DIR")
        if not log_dir_str:
            data_dir = os.getenv("DATA_DIR", "/tmp")
            log_dir_str = os.path.join(data_dir, "logs", "universal-llm-gateway")

        debug_file = Path(log_dir_str) / "current_task_debug.log"
        if debug_file.exists():
            debug_file.unlink()
    except Exception:
        pass  # Best effort
