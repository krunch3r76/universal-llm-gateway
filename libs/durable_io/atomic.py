"""Flock-serialised temp+fsync+replace with optional pre-image CAS and verify.

This is the one durable leaf every cortex-notes writer funnels through.
Serialisation across OS processes is ``fcntl.flock`` on a sibling lockfile
(``.{name}.lock``). ``os.replace`` changes the dest inode, so locking dest
itself would not survive the replace. ``threading.Lock`` is rejected: it is
per-process and does not serialise sidecar/MCP/stargate/charter writers.
"""

from __future__ import annotations

import fcntl
import hashlib
import logging
import os
import secrets
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


class PreImageMismatchError(Exception):
    """Dest bytes changed after this write's read and before replace."""

    reason = "file_sha256.mismatch"

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
            f"Pre-image CAS failed for {path}: "
            f"expected sha256 {expected_sha256!r}, got {actual_sha256!r}"
        )


_SHA256_CITATION_PREFIXES = ("sha256:", "spec_sha256:")


def _normalize_sha256_hex(value: str) -> str:
    stripped = value.strip()
    lower = stripped.lower()
    for prefix in _SHA256_CITATION_PREFIXES:
        if lower.startswith(prefix):
            return stripped[len(prefix) :].lower()
    return lower


def _sha256_hex_equal(left: str | None, right: str | None) -> bool:
    if left is None or right is None:
        return left is right
    return _normalize_sha256_hex(left) == _normalize_sha256_hex(right)


def _sha256_hex_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_hex_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def lock_path_for(dest: Path) -> Path:
    """Sibling lockfile whose inode is stable across ``os.replace`` of *dest*."""
    return dest.with_name(f".lock.{dest.name}")


@contextmanager
def path_flock(dest: Path) -> Iterator[None]:
    """Exclusive ``fcntl.flock`` on ``lock_path_for(dest)`` (cross-process)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    lock_path = lock_path_for(dest)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def temp_path_for(dest: Path) -> Path:
    """Return a unique temp path in the same directory as *dest*."""
    return dest.with_suffix(dest.suffix + f".tmp-{os.getpid()}-{secrets.token_hex(4)}")


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


def retain_existing(dest: Path, store_root: Path) -> str | None:
    """Copy existing *dest* bytes into ``store_root/.content-store/sha256``.

    Returns the bare digest when retained, or ``None`` when *dest* is absent
    or already lives under the content-store.
    """
    if not dest.is_file():
        return None
    try:
        dest.resolve().relative_to((store_root / ".content-store").resolve())
        return None
    except ValueError:
        pass
    data = dest.read_bytes()
    digest = _sha256_hex_of_bytes(data)
    store = store_root / ".content-store" / "sha256" / digest[:2] / digest
    if store.is_file() and _sha256_hex_of_file(store) == digest:
        return digest
    durable_write_bytes(store, data)
    return digest


def _write_bytes_unlocked(
    dest: Path,
    data: bytes,
    *,
    expected_pre_image: str | None,
    retain_store_root: Path | None,
) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if retain_store_root is not None:
        retain_existing(dest, retain_store_root)
    temp_path = temp_path_for(dest)
    try:
        with temp_path.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if expected_pre_image is not None:
            actual_sha256 = _sha256_hex_of_file(dest) if dest.is_file() else ""
            if actual_sha256 != expected_pre_image:
                temp_path.unlink(missing_ok=True)
                raise PreImageMismatchError(
                    dest,
                    expected_sha256=expected_pre_image,
                    actual_sha256=actual_sha256,
                )
        finalize_atomic_replace(temp_path, dest)
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise
    return _sha256_hex_of_bytes(data)


def durable_write_bytes(
    dest: Path,
    data: bytes,
    *,
    expected_pre_image: str | None = None,
    already_locked: bool = False,
    retain_store_root: Path | None = None,
) -> str:
    """Atomically write *data* to *dest*; return bare sha256 hex of *data*.

    Takes ``path_flock`` unless *already_locked* (caller holds the same lock
    across a wider RMW). When *expected_pre_image* is set, dest's digest must
    still match immediately before ``os.replace``.
    """
    if already_locked:
        return _write_bytes_unlocked(
            dest,
            data,
            expected_pre_image=expected_pre_image,
            retain_store_root=retain_store_root,
        )
    with path_flock(dest):
        return _write_bytes_unlocked(
            dest,
            data,
            expected_pre_image=expected_pre_image,
            retain_store_root=retain_store_root,
        )


def durable_write_text(
    dest: Path,
    content: str,
    *,
    expected_pre_image: str | None = None,
    already_locked: bool = False,
    retain_store_root: Path | None = None,
) -> str:
    """Atomically write UTF-8 *content* to *dest*; return bare sha256 hex."""
    return durable_write_bytes(
        dest,
        content.encode("utf-8"),
        expected_pre_image=expected_pre_image,
        already_locked=already_locked,
        retain_store_root=retain_store_root,
    )


@dataclass(frozen=True)
class RmwResult:
    """Outcome of a flock-serialised read-modify-write on a text file."""

    pre_image_sha256: str | None
    written_sha256: str
    replaced_sha256: str | None
    before_text: str
    after_text: str


def durable_rmw_text(
    dest: Path,
    transform: Callable[[str], str],
    *,
    expected_sha256: str | None = None,
    retain_store_root: Path | None = None,
    create_if_absent: bool = False,
    encoding_errors: str = "replace",
) -> RmwResult:
    """Read *dest*, apply *transform*, write back — all under one ``path_flock``.

    Caller ``expected_sha256`` is compared against the on-disk digest immediately
    after the read, still under the lock. The write uses auto-CAS against that
    same pre-image. ``verify_persisted`` runs before the lock is released.
    """
    with path_flock(dest):
        if not dest.is_file():
            if not create_if_absent:
                raise FileNotFoundError(f"File not found: {dest}")
            raw = b""
            pre_image: str | None = None
        else:
            raw = dest.read_bytes()
            pre_image = _sha256_hex_of_bytes(raw)
        if expected_sha256 is not None and not _sha256_hex_equal(
            pre_image, expected_sha256
        ):
            raise PreImageMismatchError(
                dest,
                expected_sha256=expected_sha256,
                actual_sha256=pre_image or "",
            )
        before_text = raw.decode("utf-8", errors=encoding_errors)
        after_text = transform(before_text)
        replaced_sha256 = (
            retain_existing(dest, retain_store_root)
            if retain_store_root is not None
            else None
        )
        written_sha256 = durable_write_bytes(
            dest,
            after_text.encode("utf-8"),
            expected_pre_image=pre_image,
            already_locked=True,
        )
        verify_persisted(dest, written_sha256)
        return RmwResult(
            pre_image_sha256=pre_image,
            written_sha256=written_sha256,
            replaced_sha256=replaced_sha256,
            before_text=before_text,
            after_text=after_text,
        )


def verify_persisted(dest: Path, expected_sha256: str) -> None:
    """Re-read *dest* and raise WriteVerifyError when bytes do not match."""
    actual_sha256 = _sha256_hex_of_file(dest)
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
