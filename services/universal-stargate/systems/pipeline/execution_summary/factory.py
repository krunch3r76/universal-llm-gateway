"""
Module-level factory for ``ExecutionSummaryWriter``.

Constructs the singleton-equivalent writer instance using the ``LOG_DIR``
environment variable as the parent directory. Pipeline summaries land under
``<LOG_DIR>/pipeline_summaries/``.

Separate module from ``writer.py`` to keep the class definition decoupled
from environment-variable defaults — tests can construct
``ExecutionSummaryWriter`` with an explicit ``output_dir`` without invoking
the factory's env lookup.
"""

from __future__ import annotations

import os
from pathlib import Path

from .writer import ExecutionSummaryWriter


def get_summary_writer() -> ExecutionSummaryWriter:
    """
    Get or create a summary writer instance.

    Uses the ``LOG_DIR`` environment variable when set, defaulting to ``logs``
    when unset. The summaries subdirectory (``pipeline_summaries``) is always
    appended.

    Returns:
        Configured ``ExecutionSummaryWriter`` ready for use.
    """
    log_dir = os.environ.get("LOG_DIR", "logs")
    output_dir = Path(log_dir) / "pipeline_summaries"
    return ExecutionSummaryWriter(output_dir)
