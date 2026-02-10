"""Virtual environment management."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


class EnvironmentManager:
    """Manage virtual environment detection and setup."""

    def __init__(self):
        self.venv_dir: Path | None = None
        self.is_venv: bool = False
        self.python_version: tuple[int, int] = (
            sys.version_info[0],
            sys.version_info[1],
        )
        self._validate_python_version()
        self._detect_venv()

    def _validate_python_version(self):
        """Validate Python version is 3.8 or higher."""
        if self.python_version < (3, 8):
            raise RuntimeError(
                f"❌ Python {self.python_version[0]}.{self.python_version[1]} is not supported! "
                f"This build system requires Python 3.8 or higher. "
                f"Current Python: {sys.version}"
            )
        logger.debug(
            f"Python version: {self.python_version[0]}.{self.python_version[1]}"
        )

    def _detect_venv(self):
        """Detect if running in virtual environment."""
        venv_path = os.environ.get("VIRTUAL_ENV")

        if venv_path:
            self.venv_dir = Path(venv_path)
            self.is_venv = True
            logger.info(f"✅ Virtual environment detected: {self.venv_dir}")
        else:
            # Not in venv - use sys.prefix for install location
            # sys.prefix is more reliable than site.getsitepackages()
            self.venv_dir = Path(sys.prefix)
            self.is_venv = False

            logger.warning(
                "⚠️  WARNING: No virtual environment detected! "
                f"Installation location: {self.venv_dir}"
            )

            # Prompt if interactive
            if sys.stdin.isatty():
                logger.warning(
                    "💡 It is recommended to use a virtual environment to avoid conflicts."
                )
                logger.warning("")
                response = (
                    input("Continue without a virtual environment? (yes/no): ")
                    .strip()
                    .lower()
                )
                if response not in ("yes", "y"):
                    raise RuntimeError(
                        "Aborted by user. Please activate a virtual environment first:\n"
                        "  source <venv_path>/bin/activate"
                    )
                logger.warning(
                    "⚠️  Proceeding without virtual environment (user confirmed)"
                )
            else:
                logger.warning("⚠️  Non-interactive mode - proceeding with warning")
