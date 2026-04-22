"""Context bridge tools — structured access to tasks/ directory.

Provides read/write access to journal entries, discoveries, lessons,
and other workspace context files. The tasks/ directory is mounted
read-write at _TASKS_ROOT.

Traversal protection via _safe_tasks_path() is independent of other
tool modules' path validation.
"""

from __future__ import annotations

import datetime
import logging
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from mcp_events import record

from ._file_helpers import build_binary_read_result, extract_text_content
from .file_editor import perform_edit

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_TASKS_ROOT = Path(os.environ.get("TASKS_ROOT", "/data/tasks"))
_EDITABLE_SUFFIXES = {
    ".md", ".txt", ".py", ".yaml", ".yml", ".json", ".toml",
    ".csv", ".sh", ".bash", ".js", ".ts", ".html", ".css",
    ".xml", ".ini", ".cfg", ".conf", ".env", ".log",
}
_TASKS_READ_ONLY = os.environ.get("TASKS_READ_ONLY", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def _read_only_error() -> dict[str, str]:
    return {
        "error": (
            "tasks context is read-only (TASKS_READ_ONLY=true); "
            "write tools are disabled"
        )
    }


def _record_read_only_violation(
    *,
    tool: str,
    path: str | None = None,
    operation: str | None = None,
) -> None:
    payload: dict[str, str] = {"tool": tool}
    if path is not None:
        payload["path"] = path
    if operation is not None:
        payload["operation"] = operation
    record("mcp.tool.read.only.violation", **payload)


def _safe_tasks_path(relative: str) -> Path:
    """Resolve *relative* inside the tasks root, rejecting traversal."""
    clean = relative.lstrip("/")
    resolved_root = _TASKS_ROOT.resolve()
    candidate = (resolved_root / clean).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError:
        record("mcp.tool.path.traversal.rejected", path=relative)
        raise ValueError(
            f"Path {relative!r} resolves outside tasks root; traversal rejected"
        )
    return candidate


def register_context_tools(mcp: FastMCP) -> None:
    """Register context bridge tools on *mcp*."""

    @mcp.tool(title="List Journal Entries")
    def list_journal_entries(
        limit: int = 20,
        domain: str | None = None,
    ) -> dict[str, list[dict[str, str]] | str]:
        """List recent journal entries from the workspace journal index.

        Returns summary metadata for each entry. Use read_journal_entry()
        with the slug to read the full content.

        Args:
            limit: Maximum entries to return (default 20, most recent first).
            domain: Optional domain filter (e.g. "routing", "federation").

        Returns:
            {"entries": [{"slug", "summary", "status", "domain", "opened"}, ...]}
        """
        index_path = _TASKS_ROOT / "journal" / "index.yaml"
        if not index_path.exists():
            return {"error": "journal/index.yaml not found"}

        try:
            raw_content = index_path.read_text(encoding="utf-8")
        except OSError as e:
            return {"error": f"Failed to read journal index file: {e}"}

        try:
            data = yaml.safe_load(raw_content) or {}
        except yaml.YAMLError as e:
            return {"error": f"Failed to parse journal index: {e}"}

        raw_entries = data.get("entries", [])
        filtered = []
        for entry in raw_entries:
            if domain and entry.get("domain") != domain:
                continue
            filtered.append(
                {
                    "slug": entry.get("slug", ""),
                    "summary": entry.get("summary", ""),
                    "status": entry.get("status", ""),
                    "domain": entry.get("domain", ""),
                    "opened": str(entry.get("opened", "")),
                }
            )
            if len(filtered) >= limit:
                break

        logger.info(
            "list_journal_entries: domain=%s → %d entries", domain, len(filtered)
        )
        record(
            "mcp.tool.journal.listed",
            domain=domain or "",
            limit=limit,
            count=len(filtered),
        )
        return {"entries": filtered}

    @mcp.tool(title="Read Journal Entry")
    def read_journal_entry(slug: str) -> dict[str, str]:
        """Read a journal entry by its slug.

        Args:
            slug: The journal entry slug (e.g. "busy-models-telemetry-lockup").

        Returns:
            {"content": "<full markdown content>", "slug": "<slug>"}
        """
        entry_path = _safe_tasks_path(f"journal/{slug}.md")
        if not entry_path.exists():
            return {"error": f"Journal entry not found: {slug}"}
        if not entry_path.is_file():
            return {"error": f"Not a file: {slug}"}

        content = entry_path.read_text(encoding="utf-8", errors="replace")
        logger.info("read_journal_entry: %s (%d chars)", slug, len(content))
        record("mcp.tool.journal.read", slug=slug, chars=len(content))
        return {"content": content, "slug": slug}

    @mcp.tool(title="Write Journal Entry")
    def write_journal_entry(
        slug: str,
        title: str,
        summary: str,
        domain: str,
        status: str = "open",
        files: list[str] = [],  # noqa: B006 — Pydantic handles mutable default
        content: str = "",
    ) -> dict[str, str]:
        """Create a new journal entry with proper format and index it.

        Creates journal/<slug>.md and prepends the entry to journal/index.yaml.

        Args:
            slug: Kebab-case identifier (e.g. "web-search-brave-timeout").
            title: Human-readable title for the entry header.
            summary: One-line summary for the index.
            domain: Domain tag (e.g. "tooling", "routing", "federation").
            status: Entry status (default "open").
            files: Optional list of relevant file paths.
            content: Markdown body content (Problem, Root Cause, etc.).

        Returns:
            {"status": "created", "path": "<journal entry path>"}
        """
        # Consider refactoring the read-only check into a decorator or helper.
        if _TASKS_READ_ONLY:
            _record_read_only_violation(tool="write_journal_entry")
            return _read_only_error()

        entry_path = _safe_tasks_path(f"journal/{slug}.md")
        if entry_path.exists():
            return {"error": f"Journal entry already exists: {slug}"}

        today = datetime.date.today().isoformat()
        ts = int(datetime.datetime.now(tz=datetime.UTC).timestamp())

        file_list = ", ".join(files) if files else ""

        md_lines = [
            f"# {title}",
            "",
            f"- **Opened**: {today} (unix: {ts})",
            f"- **Status**: {status}",
            f"- **Domain**: {domain}",
        ]
        if file_list:
            md_lines.append(f"- **Files**: {file_list}")
        md_lines.extend(["", content, ""])
        md_content = "\n".join(md_lines)

        entry_path.parent.mkdir(parents=True, exist_ok=True)
        entry_path.write_text(md_content, encoding="utf-8")

        index_path = _TASKS_ROOT / "journal" / "index.yaml"
        new_entry: dict[str, str | int | list[str]] = {
            "slug": slug,
            "summary": summary,
            "status": status,
            "domain": domain,
            "opened": today,
            "opened_ts": ts,
        }
        if files:
            new_entry["files"] = files

        if index_path.exists():
            try:
                raw_content = index_path.read_text(encoding="utf-8")
                index_data = yaml.safe_load(raw_content) or {}
            except OSError as e:
                logger.error("Failed to read journal index file %s: %s", index_path, e)
                return {"error": f"Failed to read journal index: {e}"}
            except yaml.YAMLError as e:
                logger.error("Failed to parse journal index %s: %s", index_path, e)
                return {"error": f"Failed to parse journal index: {e}"}
        else:
            index_data = {}

        entries = index_data.get("entries", [])
        entries.insert(0, new_entry)
        index_data["entries"] = entries
        index_path.write_text(
            yaml.dump(index_data, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )

        logger.info("write_journal_entry: created %s", slug)
        record("mcp.tool.journal.created", slug=slug)
        return {"status": "created", "path": str(entry_path)}

    @mcp.tool(title="List Context Directory")
    def list_context_directory(path: str = "") -> dict[str, list[str] | str]:
        """List files and directories under the tasks/ workspace context.

        Use this to discover what's available: journal/, discoveries/,
        lessons/, specs/, faq/, etc.

        Args:
            path: Relative path within tasks/ (empty = root).

        Returns:
            {"entries": ["<name>", ...]}
        """
        target = _safe_tasks_path(path) if path else _TASKS_ROOT
        if not target.exists():
            return {"error": f"Path not found: {path}"}
        if not target.is_dir():
            return {"error": f"Not a directory: {path}"}

        entries = sorted(p.name for p in target.iterdir())
        logger.info(
            "list_context_directory: %s → %d entries", path or "/", len(entries)
        )
        record(
            "mcp.tool.context.directory.listed", path=path or "/", count=len(entries)
        )
        return {"entries": entries}

    @mcp.tool(title="Read Context File")
    def read_context_file(path: str, binary: bool = False) -> dict[str, Any]:
        """Read a file from the tasks/ workspace context.

        Use list_context_directory() to discover available files first.

        Use ``binary=True`` when another tool needs raw bytes rather than
        decoded text.

        Args:
            path: Relative file path within tasks/ (e.g. "discoveries/index.yaml").
            binary: If True, return base64 bytes instead of decoded text.

        Returns:
            Text mode: {"content": "<file contents>", "path": "<relative path>"}
            Binary mode: {"content_base64", "mime_type", "encoding", "bytes", "path"}
        """
        target = _safe_tasks_path(path)
        if not target.exists():
            return {"error": f"File not found: {path}"}
        if not target.is_file():
            return {"error": f"Not a file: {path}"}

        if binary:
            result = build_binary_read_result(target, path_value=path)
            logger.info(
                "read_context_file: %s (%d bytes, binary)", path, result["bytes"]
            )
            record(
                "mcp.tool.context.file.read",
                path=path,
                bytes=result["bytes"],
                binary=True,
            )
            return result

        content = extract_text_content(target)
        logger.info("read_context_file: %s (%d chars)", path, len(content))
        record(
            "mcp.tool.context.file.read", path=path, chars=len(content), binary=False
        )
        return {"content": content, "path": path}

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
            record("mcp.tool.context.file.write.failed", path=path, reason="path_error")
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

    def _raise_value_error(msg: str) -> Any:
        raise ValueError(msg)

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
