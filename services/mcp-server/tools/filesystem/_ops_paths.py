"""Path operation implementations: move, copy, remove directory, delete (trash)."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from mcp_events import record

from tools._durable_write import WriteVerifyError, verify_persisted

from ._paths import (
    SANDBOX_ROOT,
    TRASH_ROOT,
    reject_template_tokens,
    safe_path,
    sha256_hex_of_file,
    trash_destination,
)
from ._share_uri_response import attach_dual_carry

logger = logging.getLogger(__name__)


def copy_file_verified(src: Path, dst: Path) -> tuple[Path, str]:
    """Copy *src* onto *dst*, then refuse unless dest bytes match source.

    ``status=copied`` is not returned by callers until this function returns.
    If *dst* already exists as a directory, the file lands at ``dst / src.name``.
    """
    if not src.is_file():
        raise ValueError(f"Source is not a file: {src}")
    dest = dst / src.name if dst.exists() and dst.is_dir() else dst
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(src), str(dest))
    if not dest.is_file():
        raise FileNotFoundError(f"Copy dest is not a file after copy: {dest}")
    digest = sha256_hex_of_file(dest)
    verify_persisted(dest, digest)
    src_digest = sha256_hex_of_file(src)
    if digest != src_digest:
        raise WriteVerifyError(
            dest,
            expected_sha256=src_digest,
            actual_sha256=digest,
        )
    return dest, digest


def move_file_impl(source: str, destination: str) -> dict[str, str]:
    """Move or rename a file within the sandbox."""
    reject_template_tokens(destination)
    src = safe_path(source, for_write=False)
    dst = safe_path(destination, for_write=True)
    if not src.exists():
        raise FileNotFoundError(f"Source not found: {source!r}")
    if not src.is_file():
        raise ValueError(f"Source is not a file: {source!r}")

    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    record("mcp.tool.file.moved", source=source, destination=destination)
    logger.info("move_file: %s → %s", src, dst)
    src_rel = source.lstrip("/")
    dst_rel = destination.lstrip("/")
    return attach_dual_carry(
        {"status": "moved", "from": src_rel, "to": dst_rel},
        sandbox="cortex",
        rel_path=dst_rel,
    )


def copy_file_impl(source: str, destination: str) -> dict[str, str]:
    """Copy a file within the sandbox."""
    reject_template_tokens(destination)
    src = safe_path(source, for_write=False)
    dst = safe_path(destination, for_write=True)
    if not src.exists():
        raise FileNotFoundError(f"Source not found: {source!r}")
    if not src.is_file():
        raise ValueError(f"Source is not a file: {source!r}")

    dest, digest = copy_file_verified(src, dst)
    record("mcp.tool.file.copied", source=source, destination=destination)
    logger.info("copy_file: %s → %s", src, dest)
    src_rel = str(src.relative_to(SANDBOX_ROOT))
    dst_rel = str(dest.relative_to(SANDBOX_ROOT))
    return attach_dual_carry(
        {
            "status": "copied",
            "from": src_rel,
            "to": dst_rel,
            "read_sha256": digest,
        },
        sandbox="cortex",
        rel_path=dst_rel,
    )


def remove_directory_impl(directory: str) -> dict[str, str]:
    """Remove a directory and all its contents from the sandbox."""
    target = safe_path(directory, for_write=True)
    if not target.exists():
        raise FileNotFoundError(f"Directory not found: {directory!r}")
    if not target.is_dir():
        raise ValueError(f"Path is not a directory: {directory!r}")
    if target == SANDBOX_ROOT:
        raise ValueError("Cannot remove the sandbox root directory")

    shutil.rmtree(str(target))
    record("mcp.tool.dir.removed", directory=directory)
    logger.info("remove_directory: %s", target)
    return {"status": "removed", "path": str(target)}


def delete_file_impl(path: str) -> dict[str, str]:
    """Soft-delete a file by moving it to the sandbox trash/ directory."""
    target = safe_path(path, for_write=True)
    if not target.exists():
        raise FileNotFoundError(f"File not found: {path!r}")
    if not target.is_file():
        raise ValueError(
            f"Path is not a file (directories cannot be deleted): {path!r}"
        )
    if target.is_relative_to(TRASH_ROOT):
        raise ValueError(
            f"File is already in trash/; use remove_directory('trash') to purge: {path!r}"
        )

    dest = trash_destination(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(target), str(dest))
    trash_rel = str(dest.relative_to(SANDBOX_ROOT))
    record(
        "mcp.tool.file.trashed",
        sandbox="cortex",
        path=path,
        trash_path=trash_rel,
    )
    logger.info("delete_file: trashed %s → %s", target, dest)
    return {"status": "trashed", "path": str(target), "trash_path": trash_rel}
