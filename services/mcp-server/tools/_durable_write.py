"""Durable atomic writes with post-persist verification.

Same-dir temp + fsync + os.replace + best-effort parent-dir fsync, then
re-read and hash-compare so callers never return success when bytes did not
land on disk.
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path
from typing import Any

from tools._hashing import sha256_hex_of_bytes, sha256_hex_of_file

logger = logging.getLogger(__name__)


class WriteVerifyError(Exception):
    """Persisted bytes on disk do not match the intended write."""

    reason = "write_verify_failed"

    def __init__(
        self,
        path: Path,
        *,
        expected_sha256: str,
        actual_sha256: str,
    ) -> None:
        self.path = path
        self.expected_sha256 = expected_sha256
        self.actual_sha256 = actual_sha256
        super().__init__(
            f"Write verification failed for {path}: "
            f"expected sha256 {expected_sha256!r}, got {actual_sha256!r}"
        )


def temp_path_for(dest: Path) -> Path:
    """Return a unique temp path in the same directory as *dest*."""
    return dest.with_suffix(
        dest.suffix + f".tmp-{os.getpid()}-{secrets.token_hex(4)}"
    )


def finalize_atomic_replace(temp_path: Path, dest: Path) -> None:
    """Fsync *temp_path*, atomically replace *dest*, best-effort dir fsync."""
    with temp_path.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(temp_path, dest)
    _best_effort_fsync_dir(dest.parent)


def _best_effort_fsync_dir(parent: Path) -> None:
    try:
        fd = os.open(str(parent), os.O_RDONLY)
    except (OSError, PermissionError, NotImplementedError):
        logger.debug("Skipping parent-dir fsync for %s", parent, exc_info=True)
        return
    try:
        os.fsync(fd)
    except (OSError, PermissionError, NotImplementedError):
        logger.debug("Parent-dir fsync failed for %s", parent, exc_info=True)
    finally:
        os.close(fd)


def durable_write_bytes(dest: Path, data: bytes) -> str:
    """Atomically write *data* to *dest*; return bare sha256 hex of *data*."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp_path = temp_path_for(dest)
    try:
        with temp_path.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        finalize_atomic_replace(temp_path, dest)
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise
    return sha256_hex_of_bytes(data)


def durable_write_text(dest: Path, content: str) -> str:
    """Atomically write UTF-8 *content* to *dest*; return bare sha256 hex."""
    return durable_write_bytes(dest, content.encode("utf-8"))


def verify_persisted(dest: Path, expected_sha256: str) -> None:
    """Re-read *dest* and raise WriteVerifyError when bytes do not match."""
    actual_sha256 = sha256_hex_of_file(dest)
    if actual_sha256 != expected_sha256:
        raise WriteVerifyError(
            dest,
            expected_sha256=expected_sha256,
            actual_sha256=actual_sha256,
        )


def write_verify_error_dict(exc: WriteVerifyError) -> dict[str, Any]:
    """Structured tool error payload for verify-after-write failure."""
    return {
        "error": str(exc),
        "reason": exc.reason,
        "path": str(exc.path),
        "expected_sha256": exc.expected_sha256,
        "actual_sha256": exc.actual_sha256,
    }
