"""Context file mutation MCP tools: write, edit, move, delete under tasks/.

Includes the editable suffix policy (_EDITABLE_SUFFIXES) for in-place edits.
All write paths enforce TASKS_READ_ONLY via tasks_path_policy and record
violations. Edit delegates to ..file_editor.perform_edit.
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

from mcp_events import record
from universal_logging import get_logger

from ..file_editor import perform_edit
from .tasks_path_policy import (
    _TASKS_READ_ONLY,
    _read_only_error,
    _record_read_only_violation,
    _safe_tasks_path,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = get_logger(__name__)


_EDITABLE_SUFFIXES = {
    ".md",
    ".txt",
    ".py",
    ".yaml",
    ".yml",
    ".json",
    ".toml",
    ".csv",
    ".sh",
    ".bash",
    ".js",
    ".ts",
    ".html",
    ".css",
    ".xml",
    ".ini",
    ".cfg",
    ".conf",
    ".env",
    ".log",
}


def register_context_file_mutation_tools(mcp: FastMCP) -> None:
    """Register write/edit/move/delete context file MCP tools."""

    @mcp.tool(title="Write Context File")
    def write_context_file(path: str, content: str) -> dict[str, str]:
        """Write a text file to the tasks/ workspace context.

        For journal entries, prefer write_journal_entry() which handles
        formatting and indexing. Use this for discoveries, lessons, specs,
        and other free-form context files.

        Args:
            path: Relative file path within tasks/ (e.g. "discoveries/new-insight.md").
            content: Text content to write.

        Returns:
            {"status": "written", "path": "<relative path>"}
        """
        if _TASKS_READ_ONLY:
            _record_read_only_violation(tool="write_context_file", path=path)
            return _read_only_error()

        try:
            target = _safe_tasks_path(path)
        except ValueError as exc:
            record("mcp.tool.context.write.failed", path=path, reason="path_error")
            return {"error": str(exc)}

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        logger.info("write_context_file: wrote %s (%d chars)", path, len(content))
        record("mcp.tool.context.file.written", path=path, chars=len(content))
        return {"status": "written", "path": path}

    @mcp.tool(title="Edit Context File")
    def edit_context_file(
        path: str,
        operation: str,
        content: str,
        line: int | None = None,
        target: str | None = None,
        all_occurrences: bool = False,
    ) -> dict[str, str | int]:
        """Atomically edit a text file in the tasks/ workspace context.

        Performs a server-side read-modify-write so the model never needs
        to read the full file content just to prepend or append.

        For structured data like journal entries, prefer specific tools
        like write_journal_entry. Use this for general text editing.

        Editing is limited to plain-text formats; binary formats must be
        written in full via write_context_file().

        Args:
            path: Relative file path (e.g. "discoveries/new-insight.md").
            operation: One of:
                - "prepend": Insert content at the beginning.
                - "append": Insert content at the end.
                - "insert_at_line": Insert at a 1-indexed line number
                  (requires `line` argument).
                - "replace": Replace occurrences of `target` string
                  (requires `target` argument).
            content: Text to insert or use as replacement.
            line: 1-indexed line number for insert_at_line.
            target: String to find for replace.
            all_occurrences: If True, replace all occurrences (default: first only).

        Returns:
            {"status": "edited: <op>", "path": "..."}
            For replace: includes "replacements_made".
            On error: {"error": "..."}
        """
        if _TASKS_READ_ONLY:
            _record_read_only_violation(
                tool="edit_context_file", path=path, operation=operation
            )
            # The cast here indicates a potential type mismatch or overly broad type.
            # _read_only_error() returns dict[str, str], which is compatible with
            # dict[str, str | int], so the cast might be unnecessary or indicate
            # a deeper type issue if 'int' is truly not expected in error returns.
            return _read_only_error()  # Type checker should handle this implicitly.

        try:
            target_path = _safe_tasks_path(path)
        except ValueError as exc:
            return {"error": str(exc)}

        if target_path.suffix.lower() not in _EDITABLE_SUFFIXES:
            return {
                "error": f"Cannot edit binary format {target_path.suffix!r} in place. "
                "Use write_context_file() instead."
            }

        try:
            if operation == "replace" and target is None:
                raise ValueError(
                    "Argument 'target' is required for 'replace' operation"
                )
            result = perform_edit(
                path=target_path,
                operation=operation,
                content=content,
                line=line,
                target_str=target,
                all_occurrences=all_occurrences,
            )
            result["path"] = path

            event_payload: dict[str, str | int | bool] = {
                "sandbox": "tasks",
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
            logger.info("edit_context_file: %s on %s", operation, path)
            return result
        except (FileNotFoundError, ValueError, OSError) as exc:
            if isinstance(exc, FileNotFoundError):
                reason = "not_found"
            elif isinstance(exc, ValueError):
                reason = "validation_error"
            else:
                reason = "os_error"
            record(
                "mcp.tool.file.edit_failed",
                sandbox="tasks",
                path=path,
                operation=operation,
                reason=reason,
                error_message=str(exc),
            )
            logger.warning("edit_context_file failed on %s: %s", path, exc)
            return {"error": str(exc)}
        except Exception as exc:
            record(
                "mcp.tool.file.edit_failed",
                sandbox="tasks",
                path=path,
                operation=operation,
                reason="unexpected_error",
                error_message=str(exc),
            )
            logger.exception(
                "edit_context_file encountered an unexpected error on %s", path
            )
            return {"error": f"An unexpected error occurred: {exc}"}

    @mcp.tool(title="Move Context File")
    def move_context_file(path: str, target: str) -> dict[str, str]:
        """Move or rename a file in the tasks/ workspace context."""
        if _TASKS_READ_ONLY:
            _record_read_only_violation(
                tool="move_context_file", path=path, operation="move"
            )
            return _read_only_error()

        try:
            src = _safe_tasks_path(path)
            dst = _safe_tasks_path(target)
        except ValueError as exc:
            return {"error": str(exc)}
        if not src.exists():
            return {"error": f"File not found: {path}"}
        if not src.is_file():
            return {"error": f"Not a file: {path}"}

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        logger.info("move_context_file: %s -> %s", path, target)
        record("mcp.tool.context.file.moved", source=path, destination=target)
        return {"status": "moved", "from": path, "to": target}

    @mcp.tool(title="Delete Context File")
    def delete_context_file(path: str) -> dict[str, str]:
        """Delete a file from the tasks/ workspace context.

        Only individual files may be deleted — directories are rejected.
        Respects TASKS_READ_ONLY: returns an error dict if set.

        Args:
            path: Relative file path within tasks/ (e.g. "discoveries/stale-note.md").

        Returns:
            {"status": "deleted", "path": "<relative path>"}
            On error: {"error": "..."}
        """
        if _TASKS_READ_ONLY:
            _record_read_only_violation(tool="delete_context_file", path=path)
            return _read_only_error()

        try:
            target = _safe_tasks_path(path)
        except ValueError as exc:
            record("mcp.tool.file.delete.failed", path=path, reason="path_error")
            return {"error": str(exc)}

        if not target.exists():
            record("mcp.tool.file.delete.failed", path=path, reason="not_found")
            return {"error": f"File not found: {path!r}"}
        if not target.is_file():
            record("mcp.tool.file.delete.failed", path=path, reason="not_file")
            return {
                "error": f"Path is not a file (directories cannot be deleted): {path!r}"
            }

        target.unlink()
        record("mcp.tool.file.deleted", sandbox="tasks", path=path)
        logger.info("delete_context_file: deleted %s", path)
        return {"status": "deleted", "path": path}
