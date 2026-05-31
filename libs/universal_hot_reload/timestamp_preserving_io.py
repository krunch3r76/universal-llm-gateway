"""
Timestamp-preserving file I/O utilities for hot-reload systems.

Prevents editors from receiving spurious file change notifications when
hot-reload systems read configuration files.
"""

import os
from pathlib import Path

from universal_logging import get_logger

logger = get_logger(__name__)


def read_text_preserving_timestamps(file_path: Path, encoding: str = "utf-8") -> str:
    """
    Read file content without modifying timestamps.

    Preserves file access/modification times to prevent editor notifications
    about spurious changes when hot-reload reads config files.

    Only restores timestamps if they actually changed during read, avoiding
    unnecessary utime() syscalls that can trigger inotify events in editors
    like Neovim.

    Args:
        file_path: Path to file to read
        encoding: Text encoding (default: utf-8)

    Returns:
        File content as string

    Raises:
        OSError: If file cannot be read
    """
    # Capture original timestamps with nanosecond precision
    stat_before = file_path.stat()
    original_atime_ns = stat_before.st_atime_ns
    original_mtime_ns = stat_before.st_mtime_ns

    try:
        # Read file content
        content = file_path.read_text(encoding=encoding)
        return content

    finally:
        # Only restore if timestamps actually changed
        # Avoids unnecessary utime() syscalls that trigger inotify events
        try:
            stat_after = file_path.stat()

            # Check if either timestamp changed
            if (
                stat_after.st_atime_ns != original_atime_ns
                or stat_after.st_mtime_ns != original_mtime_ns
            ):
                # Timestamps changed - restore them
                os.utime(file_path, ns=(original_atime_ns, original_mtime_ns))
                atime_changed = stat_after.st_atime_ns != original_atime_ns
                mtime_changed = stat_after.st_mtime_ns != original_mtime_ns
                msg = (
                    f"Restored timestamps for {file_path.name} "
                    f"(atime changed: {atime_changed}, mtime changed: {mtime_changed})"
                )
                logger.debug(msg)
        except OSError as e:
            # Log but don't fail - timestamp restoration is best-effort
            logger.debug(f"Could not restore timestamps for {file_path}: {e}")
