"""Core logic for atomic file editing operations.

Sandbox-agnostic: callers resolve paths and validate suffixes before
calling perform_edit(). This module handles file existence checks,
operation dispatch, and the atomic read-modify-write cycle.

S0 (todo:mcp-append-silent-loss): auto-CAS the digest of the bytes just
read before replace so a concurrent O_APPEND / write_text peer is a typed
mismatch instead of a silent lost-update. Caller-supplied expected_sha256
stays optional at edit_file_impl — this pre-image check is internal.
"""

from __future__ import annotations

from pathlib import Path

from tools._durable_write import (
    PreImageMismatchError,
    durable_write_text,
    path_flock,
    verify_persisted,
)
from tools._hashing import sha256_hex_of_bytes, sha256_hex_of_file

__all__ = ["PreImageMismatchError", "perform_edit"]


def perform_edit(
    path: Path,
    operation: str,
    content: str,
    *,
    line: int | None = None,
    target_str: str | None = None,
    all_occurrences: bool = False,
) -> dict[str, str | int]:
    """Read a file, apply a text operation, write it back.

    Args:
        path: Resolved path to the file (must exist).
        operation: One of prepend, append, insert_at_line, replace.
        content: Text to insert or use as replacement.
        line: 1-indexed line number for insert_at_line.
        target_str: String to find for replace.
        all_occurrences: Replace all vs first occurrence.

    Returns:
        A dictionary confirming the operation.
        Example: {"status": "edited: prepend", "path": "/path/to/file",
        "written_sha256": "<hex>"}.
        ``written_sha256`` is bare lowercase hex; callers compose
        ``sha256:`` / ``spec_sha256:`` prefixes as needed.
        For "replace" operation, also includes "replacements_made".

    Side effects:
        Overwrites *path* via temp+fsync+replace. Holds ``path_flock`` from
        read through replace so cross-process peers (sidecar / stargate /
        charter) serialise against this RMW. Auto-CASes the digest of the
        bytes read at the start of this call immediately before that
        replace; mismatch raises without writing. Does not take
        ``path_write_lock`` — ``edit_file_impl`` holds that in-process lock
        so this function stays re-entrant from a lock holder.

    Raises:
        FileNotFoundError: Path does not exist.
        ValueError: Invalid arguments or operation.
        PreImageMismatchError: Dest bytes changed after the read (loud CAS).
        WriteVerifyError: Persisted dest hash != intended write hash.
    """
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if not path.is_file():
        raise ValueError(f"Path is not a file: {path}")

    with path_flock(path):
        return _perform_edit_locked(
            path,
            operation,
            content,
            line=line,
            target_str=target_str,
            all_occurrences=all_occurrences,
        )


def _perform_edit_locked(
    path: Path,
    operation: str,
    content: str,
    *,
    line: int | None,
    target_str: str | None,
    all_occurrences: bool,
) -> dict[str, str | int]:
    """RMW body; caller holds ``path_flock``."""
    raw = path.read_bytes()
    pre_image_sha256 = sha256_hex_of_bytes(raw)
    original = raw.decode("utf-8", errors="replace")
    modified: str
    replacements_made = 0

    match operation:
        case "prepend":
            modified = content + original
        case "append":
            modified = original + content
        case "insert_at_line":
            if line is None or line < 1:
                raise ValueError(
                    "A positive 1-indexed line number is required for 'insert_at_line'."
                )
            lines = original.splitlines(keepends=True)
            if line > len(lines) + 1:
                raise ValueError(
                    f"Line {line} out of range. File has {len(lines)} lines "
                    + f"(valid: 1-{len(lines) + 1})."
                )
            insert_idx = line - 1
            insert_content = content if content.endswith("\n") else f"{content}\n"
            lines.insert(insert_idx, insert_content)
            modified = "".join(lines)
        case "replace":
            if target_str is None:
                raise ValueError(
                    "A 'target' string is required for 'replace' operation."
                )
            if target_str not in original:
                raise ValueError(f"Target string not found: {target_str!r}")
            if all_occurrences:
                replacements_made = original.count(target_str)
                modified = original.replace(target_str, content)
            else:
                replacements_made = 1
                modified = original.replace(target_str, content, 1)
        case _:
            raise ValueError(
                f"Unknown operation: {operation!r}. Must be one of: prepend, append, "
                + "insert_at_line, replace."
            )

    # Lazy import: filesystem.__init__ pulls _ops_text → file_editor; a top-level
    # import of _overwrite_retain re-enters that cycle and crash-loops mcp.
    from tools.filesystem._overwrite_retain import retain_before_overwrite

    replaced_sha256 = retain_before_overwrite(path)
    actual_sha256 = sha256_hex_of_file(path)
    if actual_sha256 != pre_image_sha256:
        raise PreImageMismatchError(
            path,
            expected_sha256=pre_image_sha256,
            actual_sha256=actual_sha256,
        )
    written_sha256 = durable_write_text(
        path,
        modified,
        expected_pre_image=pre_image_sha256,
        already_locked=True,
    )
    verify_persisted(path, written_sha256)

    result: dict[str, str | int] = {
        "status": f"edited: {operation}",
        "path": str(path),
        "written_sha256": written_sha256,
    }
    if replaced_sha256 is not None:
        result["replaced_sha256"] = replaced_sha256
    if operation == "replace":
        result["replacements_made"] = replacements_made
    return result
