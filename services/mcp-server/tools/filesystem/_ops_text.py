"""Text file operation implementations: write, read, edit, list."""

from __future__ import annotations

import logging
from typing import Any

from mcp_events import record

from .._file_helpers import read_file_result, read_files_batch
from ..file_editor import perform_edit
from ._format_writers import _write_docx, _write_pdf, _write_plain
from ._paths import (
    _EDITABLE_SUFFIXES,
    _SANDBOX_ROOT,
    _SHARED_IMAGE_DIR,
    _safe_path,
)

logger = logging.getLogger(__name__)


def write_file_impl(path: str, content: str) -> dict[str, str]:
    """Write *content* to *path* inside the sandboxed files directory.

    Intermediate directories are created automatically.
    """
    dest = _safe_path(path)
    suffix = dest.suffix.lower()
    try:
        write_handlers = {
            ".docx": _write_docx,
            ".pdf": _write_pdf,
        }
        write_handler = write_handlers.get(suffix, _write_plain)
        write_handler(dest, content)
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

    record(
        "mcp.tool.file.written", path=path, resolved=str(dest), chars=len(content)
    )
    logger.debug("write_file: wrote %s (%d chars)", dest, len(content))
    return {"status": "written", "path": str(dest)}


def read_file_impl(path: str, binary: bool = False) -> dict[str, Any]:
    """Read and return the contents of *path* from the sandboxed directory."""
    result = read_file_result(path, binary=binary)
    record(
        "mcp.tool.file.read",
        path=path,
        resolved=result["path"],
        binary=binary,
        chars=len(result["content"]) if "content" in result else 0,
        bytes=result.get("bytes", 0),
    )
    logger.debug(
        "read_file: read %s (%s)",
        result["path"],
        f"{result.get('bytes', 0)} bytes"
        if binary
        else f"{len(result['content'])} chars",
    )
    return result


def read_files_batch_impl(paths: list[str], binary: bool = False) -> dict[str, Any]:
    """Batch-read multiple files, recording an event per file."""
    results = read_files_batch(paths, binary=binary)
    for batch_path, batch_result in results.items():
        if isinstance(batch_result, str):
            record(
                "mcp.tool.file.read",
                path=batch_path,
                resolved=str(_safe_path(batch_path)),
                chars=len(batch_result),
                batched=True,
                binary=False,
            )
        elif isinstance(batch_result, dict) and "content_base64" in batch_result:
            record(
                "mcp.tool.file.read",
                path=batch_path,
                resolved=str(_safe_path(batch_path)),
                bytes=batch_result.get("bytes", 0),
                batched=True,
                binary=True,
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
    dest = _safe_path(path)
    if dest.suffix.lower() not in _EDITABLE_SUFFIXES:
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
    except (FileNotFoundError, ValueError) as exc:
        reason = (
            "not_found"
            if isinstance(exc, FileNotFoundError)
            else "validation_error"
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
    target = _safe_path(directory) if directory else _SANDBOX_ROOT
    if not target.exists():
        return {"files": []}
    if not target.is_dir():
        raise ValueError(f"Path is not a directory: {directory!r}")

    generated_dir = _SHARED_IMAGE_DIR.resolve()
    files = sorted(
        str(p.relative_to(_SANDBOX_ROOT))
        for p in target.rglob("*")
        if p.is_file() and not p.is_relative_to(generated_dir)
    )
    record("mcp.tool.file.listed", directory=directory or ".", count=len(files))
    logger.debug("list_files: %s → %d files", target, len(files))
    return {"files": files}
