"""Path operation implementations: move, copy, remove directory, delete (trash)."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from mcp_events import record

from tools._durable_write import (
    PreImageMismatchError,
    SourceDigestMismatchError,
    durable_copy_file,
    path_flock,
)
from tools._hashing import (
    format_sha256_uri,
    normalize_sha256_hex,
    sha256_hex_equal,
    sha256_hex_of_file,
    sha256_of_file,
)

from ._overwrite_retain import retain_before_overwrite
from ._paths import (
    SANDBOX_ROOT,
    TRASH_ROOT,
    path_write_lock,
    reject_template_tokens,
    safe_path,
    trash_destination,
)
from ._share_uri_response import attach_dual_carry
from ._write_authority import evaluate_write_authority

logger = logging.getLogger(__name__)


def _resolve_copy_dest(dst: Path, src: Path) -> Path:
    return dst / src.name if dst.exists() and dst.is_dir() else dst


def _under_cortex_sandbox(dest: Path) -> bool:
    try:
        dest.resolve().relative_to(SANDBOX_ROOT.resolve())
    except ValueError:
        return False
    return True


def source_digest_mismatch_dict(exc: SourceDigestMismatchError) -> dict[str, str]:
    return {
        "error": str(exc),
        "reason": exc.reason,
        "expected_read_sha256": format_sha256_uri(exc.expected_sha256),
        "actual_sha256": format_sha256_uri(exc.actual_sha256),
    }


def pre_image_mismatch_dict(exc: PreImageMismatchError) -> dict[str, str]:
    return {
        "error": str(exc),
        "reason": exc.reason,
        "path": str(exc.path),
        "expected_sha256": format_sha256_uri(exc.expected_sha256),
        "actual_sha256": format_sha256_uri(exc.actual_sha256),
    }


def _cortex_copy_authority_refusal(
    dest_rel: str,
    dest: Path,
    *,
    source_digest: str,
    if_absent: bool,
    expected_sha256: str | None,
) -> dict[str, Any] | None:
    if if_absent and expected_sha256:
        return {
            "error": "expected_sha256 and if_absent are mutually exclusive",
            "reason": "invalid_params",
        }
    actual_sha256 = sha256_of_file(dest)
    authority = evaluate_write_authority(
        path=dest_rel,
        content="",
        dest_exists=dest.is_file(),
        actual_sha256=actual_sha256,
        expected_sha256=expected_sha256,
        if_absent=if_absent,
        attempted_sha256=source_digest,
    )
    if authority is not None:
        return authority
    if if_absent and dest.exists():
        return {
            "error": f"Refusing to overwrite existing file: {dest_rel!r}",
            "reason": "file_exists",
            "path": dest_rel.lstrip("/"),
            "existing_sha256": (
                None
                if actual_sha256 is None
                else format_sha256_uri(normalize_sha256_hex(actual_sha256))
            ),
        }
    if expected_sha256 is not None and dest.is_file():
        pre_image = sha256_hex_of_file(dest)
        if not sha256_hex_equal(pre_image, expected_sha256):
            return {
                "error": (
                    f"Destination sha256 mismatch for {dest_rel!r}: "
                    f"expected {expected_sha256!r}, got {pre_image!r}"
                ),
                "reason": "file_sha256.mismatch",
                "path": dest_rel.lstrip("/"),
                "expected_sha256": format_sha256_uri(expected_sha256),
                "actual_sha256": format_sha256_uri(pre_image),
            }
    return None


def copy_file_verified(
    src: Path,
    dst: Path,
    *,
    dest_rel: str | None = None,
    if_absent: bool = False,
    expected_sha256: str | None = None,
    expected_source_digest: str | None = None,
) -> tuple[Path, str, int, str | None]:
    """Copy *src* onto *dst* with durable persistence; return (dest, digest, nbytes, replaced?)."""
    if not src.is_file():
        raise ValueError(f"Source is not a file: {src}")
    dest = _resolve_copy_dest(dst, src)
    if dest_rel is None:
        try:
            dest_rel = str(dest.relative_to(SANDBOX_ROOT.resolve()))
        except ValueError:
            dest_rel = dest.as_posix()
    source_digest = sha256_hex_of_file(src)
    if expected_source_digest is not None and not sha256_hex_equal(
        source_digest, expected_source_digest
    ):
        raise SourceDigestMismatchError(
            expected_sha256=normalize_sha256_hex(expected_source_digest),
            actual_sha256=source_digest,
        )

    use_cortex_gate = _under_cortex_sandbox(dest)
    pre_image = sha256_hex_of_file(dest) if dest.is_file() else None
    if use_cortex_gate:
        with path_write_lock(dest):
            with path_flock(dest):
                refusal = _cortex_copy_authority_refusal(
                    dest_rel,
                    dest,
                    source_digest=source_digest,
                    if_absent=if_absent,
                    expected_sha256=expected_sha256,
                )
                if refusal is not None:
                    raise CopyRefusalError(refusal)
                replaced_sha256 = (
                    retain_before_overwrite(dest) if dest.is_file() else None
                )
                expected_pre = (
                    normalize_sha256_hex(expected_sha256)
                    if expected_sha256 and dest.is_file()
                    else None
                )
                digest, nbytes = durable_copy_file(
                    src,
                    dest,
                    expected_source_digest=source_digest,
                    expected_pre_image=expected_pre,
                    already_locked=True,
                )
    else:
        digest, nbytes = durable_copy_file(
            src,
            dest,
            expected_source_digest=source_digest,
        )
        replaced_sha256 = None
        if pre_image is not None and dest.is_file():
            replaced_sha256 = pre_image
    return dest, digest, nbytes, replaced_sha256


class CopyRefusalError(Exception):
    """Structured copy rejection raised from the cortex authority gate."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        super().__init__(str(payload.get("error")))


def copy_file_impl(source: str, destination: str) -> dict[str, str]:
    """Copy a file within the sandbox."""
    reject_template_tokens(destination)
    src = safe_path(source, for_write=False)
    dst = safe_path(destination, for_write=True)
    if not src.exists():
        raise FileNotFoundError(f"Source not found: {source!r}")
    if not src.is_file():
        raise ValueError(f"Source is not a file: {source!r}")

    dest, digest, _nbytes, replaced = copy_file_verified(
        src, dst, dest_rel=destination.lstrip("/")
    )
    record("mcp.tool.file.copied", source=source, destination=destination)
    logger.info("copy_file: %s → %s", src, dest)
    src_rel = str(src.relative_to(SANDBOX_ROOT))
    dst_rel = str(dest.relative_to(SANDBOX_ROOT))
    payload: dict[str, str] = {
        "status": "copied",
        "from": src_rel,
        "to": dst_rel,
        "read_sha256": digest,
    }
    if replaced is not None:
        payload["replaced_sha256"] = replaced
    return attach_dual_carry(
        payload,
        sandbox="cortex",
        rel_path=dst_rel,
    )


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


__all__ = [
    "CopyRefusalError",
    "copy_file_impl",
    "copy_file_verified",
    "delete_file_impl",
    "move_file_impl",
    "pre_image_mismatch_dict",
    "remove_directory_impl",
    "source_digest_mismatch_dict",
]