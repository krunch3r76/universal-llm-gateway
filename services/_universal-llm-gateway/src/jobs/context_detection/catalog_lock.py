"""Exclusive file lock for concurrent-safe local catalog writes during measurement.

Uses fcntl flock on a sidecar lock file adjacent to the catalog path so parallel
measurement jobs cannot corrupt shared local catalog YAML on Unix hosts.
"""

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def catalog_write_lock(catalog_path: Path) -> Iterator[None]:
    """
    Acquire exclusive write lock on catalog file.

    Prevents concurrent measurements from corrupting the catalog.
    Uses fcntl for file-level locking. Unix-only — not portable to Windows.
    """
    lock_file = catalog_path.parent / f".{catalog_path.name}.lock"
    lock_file.touch(exist_ok=True)

    with open(lock_file, "w") as lock_fd:
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
