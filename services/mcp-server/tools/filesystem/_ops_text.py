"""Text file operation implementations: write, read, edit, list."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from mcp_events import record

from .._durable_write import (
    WriteVerifyError,
    durable_write_text,
    finalize_atomic_replace,
    temp_path_for,
    verify_persisted,
    write_verify_error_dict,
)
from .._file_helpers import read_file_result, read_files_batch
from ..file_editor import perform_edit
from ._format_writers import write_docx, write_pdf
from ._paths import (
    EDITABLE_SUFFIXES,
    SANDBOX_ROOT,
    SHARED_IMAGE_DIR,
    path_write_lock,
    reject_template_tokens,
    safe_path,
    sha256_hex_of_file,
    sha256_of_file,
)
from ._share_uri_response import attach_dual_carry

logger = logging.getLogger(__name__)


def _write_rejection(
    *,
    path: str,
    resolved: Path,
    reason: str,
    message: str,
    expected_sha256: str | None = None,
    actual_sha256: str | None = None,
) -> dict[str, Any]:
    record(
        "mcp.tool.file.write_rejected",
        path=path,
        resolved=str(resolved),
        reason=reason,
        expected_sha256=expected_sha256,
        actual_sha256=actual_sha256,
    )
    payload: dict[str, Any] = {
        "error": message,
        "reason": reason,
        "path": path.lstrip("/"),
    }
    if expected_sha256 is not None:
        payload["expected_sha256"] = expected_sha256
    if actual_sha256 is not None:
        payload["actual_sha256"] = actual_sha256
    return payload


def _write_content_durable(dest: Path, content: str) -> str:
    """Write *content* to *dest* with fsync + atomic replace; return sha256 hex."""
    suffix = dest.suffix.lower()
    if suffix in {".docx", ".pdf"}:
        write_handlers = {
            ".docx": write_docx,
            ".pdf": write_pdf,
        }
        temp_path = temp_path_for(dest)
        try:
            write_handlers[suffix](temp_path, content)
            finalize_atomic_replace(temp_path, dest)
        except Exception:
            if temp_path.exists():
                temp_path.unlink()
            raise
        written_sha256 = sha256_hex_of_file(dest)
        verify_persisted(dest, written_sha256)
        return written_sha256
    written_sha256 = durable_write_text(dest, content)
    verify_persisted(dest, written_sha256)
    return written_sha256


def write_file_impl(
    path: str,
    content: str,
    *,
    expected_sha256: str | None = None,
    if_absent: bool = False,
) -> dict[str, Any]:
    """Write *content* to *path* inside the sandboxed files directory.

    Intermediate directories are created automatically.

    CAS semantics (cortex sandbox; see friction-13695 sidecar):
      - ``expected_sha256`` absent → legacy create-or-overwrite.
      - ``expected_sha256`` present → file must exist and hash must match.
      - ``if_absent=True`` → create-only; fails when the path already exists.
      - Both guard params together → ``ValueError``.

    On success, response includes ``written_sha256``: bare lowercase hex of the
    resulting file bytes. Callers compose ``sha256:`` / ``spec_sha256:`` prefixes.
    """
    if expected_sha256 is not None and if_absent:
        raise ValueError("expected_sha256 and if_absent are mutually exclusive")

    reject_template_tokens(path)
    dest = safe_path(path)
    with path_write_lock(dest):
        actual_sha256 = sha256_of_file(dest)
        if if_absent and dest.exists():
            return _write_rejection(
                path=path,
                resolved=dest,
                reason="file_exists",
                message=f"Refusing to overwrite existing file: {path!r}",
            )
        if expected_sha256 is not None and actual_sha256 != expected_sha256:
            return _write_rejection(
                path=path,
                resolved=dest,
                reason="file_sha256.mismatch",
                message=(
                    f"Refusing write to {path!r}: current file hash "
                    f"{actual_sha256!r} does not match expected {expected_sha256!r}"
                ),
                expected_sha256=expected_sha256,
                actual_sha256=actual_sha256,
            )
        try:
            written_sha256 = _write_content_durable(dest, content)
        except WriteVerifyError as exc:
            return write_verify_error_dict(exc)
        except OSError as exc:
            record(
                "mcp.tool.file.write_failed",
                path=path,
                resolved=str(dest),
                reason="os_error",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            logger.exception("write_file: OS error writing %s", dest)
            raise

    record("mcp.tool.file.written", path=path, resolved=str(dest), chars=len(content))
    logger.debug("write_file: wrote %s (%d chars)", dest, len(content))
    rel = path.lstrip("/")
    return attach_dual_carry(
        {
            "status": "written",
            "written_sha256": written_sha256,
        },
        sandbox="cortex",
        rel_path=rel,
    )


def read_file_impl(
    path: str, binary: bool = False, offset: int = 0, limit: int = 0
) -> dict[str, Any]:
    """Read and return the contents of *path* from the sandboxed directory."""
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
) -> dict[str, str | int]:
    """Atomically edit a text file in the sandboxed files directory."""
    reject_template_tokens(path)
    dest = safe_path(path)
    if dest.suffix.lower() not in EDITABLE_SUFFIXES:
        raise ValueError(
            f"Cannot edit binary format {dest.suffix!r} in place. "
            f"Use write_file() instead."
        )

    try:
        result = perform_edit(
            path=dest,
            operation=operation,
            content=content,
            line=line,
            target_str=target,
            all_occurrences=all_occurrences,
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


def list_files_impl(directory: str = "") -> dict[str, list[str]]:
    """List files in *directory* within the sandboxed files directory."""
    target = safe_path(directory) if directory else SANDBOX_ROOT
    if not target.exists():
        return {"files": []}
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
    return {"files": files}
