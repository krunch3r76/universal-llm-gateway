"""Core logic for atomic file editing operations.

Sandbox-agnostic: callers resolve paths and validate suffixes before
calling perform_edit(). This module handles file existence checks,
operation dispatch, and the atomic read-modify-write cycle.
"""

from __future__ import annotations

from pathlib import Path

from tools._durable_write import durable_write_text, verify_persisted
from tools.filesystem._overwrite_retain import retain_before_overwrite


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

    Raises:
        FileNotFoundError: Path does not exist.
        ValueError: Invalid arguments or operation.
    """
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if not path.is_file():
        raise ValueError(f"Path is not a file: {path}")

    original = path.read_text(encoding="utf-8", errors="replace")
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

    replaced_sha256 = retain_before_overwrite(path)
    written_sha256 = durable_write_text(path, modified)
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
