"""
Bootstrap logging for universal_logging initialization.

Provides minimal, dependency-free logging during universal_logging setup.
Prevents circular dependencies and enables configurable observability.

Environment Variables:
    UNIVERSAL_LOGGING_BOOTSTRAP: Logging level (SILENT, ERROR, INFO, DEBUG)
        - SILENT (default): No output (production)
        - ERROR: Only critical failures
        - INFO: Setup progress messages
        - DEBUG: Detailed diagnostics

    UNIVERSAL_LOGGING_VERIFY_HANDLERS: Enable handler verification
        - Default: auto (enabled only if bootstrap >= DEBUG)
        - Values: 0, 1, false, true, no, yes

Usage:
    from universal_logging.bootstrap import bootstrap_logger

    bootstrap_logger.info("Loading configuration")
    bootstrap_logger.debug(f"Config: {config}")
    bootstrap_logger.error(f"Failed: {error}")

Invariant: ∀ call: ¬depends_on(universal_logging_runtime)
"""

import os
import sys
from enum import IntEnum
from typing import TextIO


class BootstrapLevel(IntEnum):
    """Bootstrap logging levels (not Python logging levels)."""

    SILENT = 0  # Production: no output
    ERROR = 1  # Only critical errors
    INFO = 2  # Setup progress
    DEBUG = 3  # Detailed diagnostics


class BootstrapLogger:
    """
    Minimal logger for universal_logging initialization.

    Design:
    - Uses stderr directly (no logging module dependency)
    - Configured via environment variable
    - Thread-safe (atomic writes to stderr)
    - Zero dependencies on universal_logging infrastructure

    Invariant: ∀ call: ¬depends_on(universal_logging_runtime)
    """

    def __init__(
        self, stream: TextIO | None = None, level: BootstrapLevel | None = None
    ):
        """
        Initialize bootstrap logger.

        Args:
            stream: Output stream (default: stderr)
            level: Bootstrap level (default: from UNIVERSAL_LOGGING_BOOTSTRAP env)
        """
        self.stream = stream or sys.stderr

        if level is not None:
            self.level = level
        else:
            # Read from environment
            level_str = os.getenv("UNIVERSAL_LOGGING_BOOTSTRAP", "SILENT").upper()
            try:
                self.level = BootstrapLevel[level_str]
            except KeyError:
                # Invalid value: default to SILENT (production safe)
                self.level = BootstrapLevel.SILENT

    def error(self, msg: str) -> None:
        """
        Log error message (critical failures only).

        Args:
            msg: Error message
        """
        if self.level >= BootstrapLevel.ERROR:
            print(f"[universal_logging:bootstrap] ERROR: {msg}", file=self.stream)

    def info(self, msg: str) -> None:
        """
        Log info message (setup progress).

        Args:
            msg: Info message
        """
        if self.level >= BootstrapLevel.INFO:
            print(f"[universal_logging:bootstrap] INFO: {msg}", file=self.stream)

    def debug(self, msg: str) -> None:
        """
        Log debug message (detailed diagnostics).

        Args:
            msg: Debug message
        """
        if self.level >= BootstrapLevel.DEBUG:
            print(f"[universal_logging:bootstrap] DEBUG: {msg}", file=self.stream)

    def is_debug(self) -> bool:
        """Check if debug level enabled."""
        return self.level >= BootstrapLevel.DEBUG

    def is_silent(self) -> bool:
        """Check if silent (production default)."""
        return self.level == BootstrapLevel.SILENT


# Create singleton at module level
bootstrap_logger = BootstrapLogger()
