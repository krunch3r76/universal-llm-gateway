"""Text file operation implementations: read, edit, list (write in ``_ops_write``)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from mcp_events import record

from .._durable_write import (
    PreImageMismatchError,
    WriteVerifyError,
    write_verify_error_dict,
)
from .._file_helpers import read_file_result, read_files_batch
from .._hashing import format_sha256_uri, sha256_hex_equal
from ..file_editor import perform_edit
from ._ops_write import write_file_impl, write_rejection
from ._paths import (
    EDITABLE_SUFFIXES,
    SANDBOX_ROOT,
    SHARED_IMAGE_DIR,
    path_write_lock,
    reject_template_tokens,
    safe_path,
    sha256_of_file,
)
from ._share_uri_response import attach_dual_carry
from ._write_authority import evaluate_write_authority

logger = logging.getLogger(__name__)

__all__ = [
    "write_file_impl",
    "read_file_impl",
    "read_files_batch_impl",
    "edit_file_impl",
    "list_files_impl",
]


def read_file_impl(
    path: str, binary: bool = False, offset: int = 0, limit: int = 0
) -> dict[str, Any]:
    """Read and return the contents of *path* from the sandboxed directory.

    Response includes ``read_sha256``: bare lowercase hex of on-disk source bytes
    (full file; independent of offset/limit windowing on ``content``).
    """
    result = read_file_result(path, binary=binary, offset=offset, limit=limit)
    auto_binary = bool(result.get("auto_binary"))
    range_requested = offset > 0 or limit > 0
    event_payload: dict[str, Any] = {
        "path": path,
        "resolved": result["path"],
        "binary": binary or auto_binary,
        "auto_binary": auto_binary,
        "chars": len(result["content"]) if "content" in result else 0,
        "bytes": result.get("bytes", 0),
    }
    if range_requested:
        line_range = result.get("line_range", {})
        event_payload["offset"] = offset
        event_payload["limit"] = limit
        event_payload["returned_lines"] = line_range.get("returned", 0)
        event_payload["total_lines"] = result.get("total_lines", 0)
    record("mcp.tool.file.read", **event_payload)
    logger.debug(
        "read_file: read %s (%s)",
        result.get("path", path),
        f"{result.get('bytes', 0)} bytes"
        if (binary or auto_binary)
        else f"{len(result.get('content', ''))} chars",
    )
    rel = path.lstrip("/")
    out = dict(result)
    if isinstance(out.get("path"), str) and out["path"].startswith("/"):
        out["path"] = rel
    return attach_dual_carry(out, sandbox="cortex", rel_path=rel)


def read_files_batch_impl(paths: list[str], binary: bool = False) -> dict[str, Any]:
    """Batch-read multiple files, recording an event per file."""
    results = read_files_batch(paths, binary=binary)
    for batch_path, batch_result in results.items():
        if isinstance(batch_result, str):
            record(
                "mcp.tool.file.read",
                path=batch_path,
                resolved=str(safe_path(batch_path)),
                chars=len(batch_result),
                batched=True,
                binary=False,
                auto_binary=False,
            )
        elif isinstance(batch_result, dict) and "content_base64" in batch_result:
            auto_binary = bool(batch_result.get("auto_binary"))
            record(
                "mcp.tool.file.read",
                path=batch_path,
                resolved=str(safe_path(batch_path)),
                bytes=batch_result.get("bytes", 0),
                batched=True,
                binary=True,
                auto_binary=auto_binary,
            )
    logger.debug("files: batch read %d file(s)", len(paths))
    return {"files": results}


def edit_file_impl(
    path: str,
    operation: str,
    content: str,
    line: int | None = None,
    target: str | None = None,
    all_occurrences: bool = False,
    expected_sha256: str | None = None,
    artifact_class: str | None = None,
) -> dict[str, Any]:
    """Atomically edit a text file in the sandboxed files directory.

    Holds ``path_write_lock`` from pre-image hash through ``perform_edit``
    replace so in-process MCP append/replace/prepend/insert serialize against
    each other and against ``write_file_impl`` (same lock). Does not re-enter
    the lock inside ``perform_edit`` — ``threading.Lock`` is non-reentrant.

    Caller ``expected_sha256`` stays optional and is strict when supplied.
    ``perform_edit`` additionally auto-CASes the digest it just read.
    """
    reject_template_tokens(path)
    dest = safe_path(path, for_write=True)
    if dest.suffix.lower() not in EDITABLE_SUFFIXES:
        raise ValueError(
            f"Cannot edit binary format {dest.suffix!r} in place. "
            f"Use write_file() instead."
        )
    with path_write_lock(dest):
        return _edit_file_impl_locked(
            path=path,
            dest=dest,
            operation=operation,
            content=content,
            line=line,
            target=target,
            all_occurrences=all_occurrences,
            expected_sha256=expected_sha256,
            artifact_class=artifact_class,
        )


def _edit_file_impl_locked(
    *,
    path: str,
    dest: Path,
    operation: str,
    content: str,
    line: int | None,
    target: str | None,
    all_occurrences: bool,
    expected_sha256: str | None,
    artifact_class: str | None,
) -> dict[str, Any]:
    """Run the RMW under ``path_write_lock`` already held by ``edit_file_impl``."""
    actual_sha256 = sha256_of_file(dest)
    class_decision = evaluate_write_authority(
        path=path,
        content=content,
        dest_exists=dest.exists(),
        actual_sha256=actual_sha256,
        expected_sha256=expected_sha256,
        if_absent=False,
        artifact_class=artifact_class,
    )
    if (
        class_decision is not None
        and class_decision.get("reason") == "expected_sha256.required"
    ):
        return write_rejection(
            path=path,
            resolved=dest,
            reason=str(class_decision["reason"]),
            message=str(class_decision["error"]),
            actual_sha256=class_decision.get("actual_sha256"),
            artifact_class=class_decision.get("artifact_class"),
        )
    if class_decision is not None and class_decision.get("artifact_class") == "consult":
        return write_rejection(
            path=path,
            resolved=dest,
            reason="consult_class.in_place_edit",
            message=(
                f"Refusing in-place {operation} on consult-class path {path!r}; "
                "mint a distinct seat+execution_id address with if_absent=true"
            ),
            actual_sha256=(
                None if actual_sha256 is None else format_sha256_uri(actual_sha256)
            ),
            artifact_class="consult",
        )
    if expected_sha256 is not None and not sha256_hex_equal(
        actual_sha256, expected_sha256
    ):
        expected_echo = format_sha256_uri(expected_sha256)
        actual_echo = (
            None if actual_sha256 is None else format_sha256_uri(actual_sha256)
        )
        return write_rejection(
            path=path,
            resolved=dest,
            reason="file_sha256.mismatch",
            message=(
                f"Refusing {operation} on {path!r}: current file hash "
                f"{actual_echo!r} does not match expected {expected_echo!r}"
            ),
            expected_sha256=expected_echo,
            actual_sha256=actual_echo,
        )

    try:
        result = perform_edit(
            path=dest,
            operation=operation,
            content=content,
            line=line,
            target_str=target,
            all_occurrences=all_occurrences,
            expected_sha256=expected_sha256,
        )
        event_payload: dict[str, str | int | bool] = {
            "sandbox": "cortex",
            "path": path,
            "operation": operation,
            "content_chars": len(content),
        }
        if line is not None:
            event_payload["line"] = line
        if target is not None:
            event_payload["target_chars"] = len(target)
        if operation == "replace":
            event_payload["all_occurrences"] = all_occurrences
            event_payload["replacements_made"] = result.get("replacements_made", 0)
        record("mcp.tool.file.edited", **event_payload)
        logger.info("edit_file: %s on %s", operation, path)
        return result
    except PreImageMismatchError as exc:
        record(
            "mcp.tool.file.edit_failed",
            sandbox="cortex",
            path=path,
            operation=operation,
            reason=exc.reason,
            expected_sha256=exc.expected_sha256,
            actual_sha256=exc.actual_sha256,
        )
        expected_echo = format_sha256_uri(exc.expected_sha256)
        actual_echo = format_sha256_uri(exc.actual_sha256)
        return write_rejection(
            path=path,
            resolved=dest,
            reason="file_sha256.mismatch",
            message=(
                f"Refusing {operation} on {path!r}: on-disk hash "
                f"{actual_echo!r} does not match pre-image {expected_echo!r} "
                "read at the start of this edit"
            ),
            expected_sha256=expected_echo,
            actual_sha256=actual_echo,
        )
    except WriteVerifyError as exc:
        record(
            "mcp.tool.file.edit_failed",
            sandbox="cortex",
            path=path,
            operation=operation,
            reason=exc.reason,
            expected_sha256=exc.expected_sha256,
            actual_sha256=exc.actual_sha256,
        )
        return write_verify_error_dict(exc)
    except (FileNotFoundError, ValueError) as exc:
        reason = (
            "not_found" if isinstance(exc, FileNotFoundError) else "validation_error"
        )
        record(
            "mcp.tool.file.edit_failed",
            sandbox="cortex",
            path=path,
            operation=operation,
            reason=reason,
            error_message=str(exc),
        )
        logger.exception("edit_file failed on %s", path)
        raise


def list_files_impl(directory: str = "") -> dict[str, list[str] | str | int]:
    """List files in *directory* within the sandboxed files directory."""
    target = safe_path(directory) if directory else SANDBOX_ROOT
    if not target.exists():
        return {
            "files": [],
            "status": "path_not_found",
            "observation": (
                f"Path does not exist: {directory or '.'!r}. "
                "Listing succeeded with zero entries — not an empty directory."
            ),
        }
    if not target.is_dir():
        raise ValueError(f"Path is not a directory: {directory!r}")

    generated_dir = SHARED_IMAGE_DIR.resolve()
    files = sorted(
        str(p.relative_to(SANDBOX_ROOT))
        for p in target.rglob("*")
        if p.is_file() and not p.is_relative_to(generated_dir)
    )
    record("mcp.tool.file.listed", directory=directory or ".", count=len(files))
    logger.debug("list_files: %s → %d files", target, len(files))
    if not files:
        return {
            "files": [],
            "status": "empty_directory",
            "count": 0,
            "observation": (
                "Directory exists and contains no files (excluding generated images). "
                "Listing succeeded with zero entries."
            ),
        }
    return {"files": files, "status": "ok", "count": len(files)}
