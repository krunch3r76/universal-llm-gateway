"""Backup utilities for catalog split."""

from datetime import UTC, datetime
from pathlib import Path


def create_timestamped_backup(source_path: Path) -> Path:
    """
    Create timestamped backup of source file.

    Args:
        source_path: Path to file to backup

    Returns:
        Path to created backup file

    Raises:
        IOError: If backup creation fails
    """
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_path = source_path.with_name(f"{source_path.name}.bak.{ts}")
    backup_path.write_bytes(source_path.read_bytes())
    return backup_path
