"""Context bridge tools — structured access to tasks/ directory.

Provides read/write access to todo items, journal entries, discoveries,
lessons, and other workspace context files. The tasks/ directory is
mounted read-write at _TASKS_ROOT.

Traversal protection via _safe_tasks_path() is independent of other
tool modules' path validation.
"""

from __future__ import annotations

import datetime
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, cast

import yaml
from mcp_events import record

from .file_editor import perform_edit

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_TASKS_ROOT = Path(os.environ.get("TASKS_ROOT", "/data/tasks"))
_TODOS_DB = Path(os.environ.get("TODOS_DB", "/data/cortex/todos.db"))
_ALLOWED_WRITE_SUFFIXES = {".md", ".txt", ".yaml", ".yml"}
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

    @mcp.tool()
    def list_todos(
        status: str = "open",
        domain: str | None = None,
        context: str | None = None,
        priority: str | None = None,
        limit: int = 30,
    ) -> dict[str, list[dict[str, str]] | str]:
        """List todo items from the todos database.

        Filters by status, domain, context, and/or priority. Returns
        structured items for quick scanning without loading full descriptions.

        Args:
            status: Filter by status (default "open"). Use "all" for everything.
            domain: Optional domain filter (substring match, e.g. "routing").
            context: Optional context filter (e.g. "universal-llm-gateway").
            priority: Optional priority filter (e.g. "short_term", "high").
            limit: Maximum items to return (default 30).

        Returns:
            {"items": [{"id", "title", "status", "priority", "domain", "context"}, ...]}
        """
        import sqlite3 as _sqlite3

        db_path = _TODOS_DB
        if not db_path.exists():
            return {"error": f"todos.db not found at {db_path}"}

        clauses: list[str] = []
        params: list[str] = []

        if status != "all":
            clauses.append("status = ?")
            params.append(status)
        if domain:
            clauses.append("domain LIKE ?")
            params.append(f"%{domain}%")
        if context:
            clauses.append("context = ?")
            params.append(context)
        if priority:
            clauses.append("priority = ?")
            params.append(priority)

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT id, title, status, priority, domain, context FROM todos{where} ORDER BY rowid LIMIT ?"
        params.append(str(limit))

        try:
            conn = _sqlite3.connect(str(db_path))
            conn.row_factory = _sqlite3.Row
            rows = conn.execute(sql, params).fetchall()
            conn.close()
        except _sqlite3.Error as exc:
            return {"error": f"DB query failed: {exc}"}

        items = [dict(row) for row in rows]

        logger.info(
            "list_todos: status=%s domain=%s context=%s → %d items",
            status,
            domain,
            context,
            len(items),
        )
        record(
            "mcp.tool.todo.listed",
            status=status,
            domain=domain or "",
            context=context or "",
            priority=priority or "",
            limit=limit,
            count=len(items),
        )
        return {"items": items}

    @mcp.tool()
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
            if domain and domain not in (entry.get("domain") or ""):
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

    @mcp.tool()
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

    @mcp.tool()
    def write_journal_entry(
        slug: str,
        title: str,
        summary: str,
        domain: str,
        status: str = "open",
        files: list[str] | None = None,
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
        if _TASKS_READ_ONLY:
            _record_read_only_violation(tool="write_journal_entry")
            return _read_only_error()

        entry_path = _safe_tasks_path(f"journal/{slug}.md")
        if entry_path.exists():
            return {"error": f"Journal entry already exists: {slug}"}

        today = datetime.date.today().isoformat()
        ts = int(datetime.datetime.now(tz=datetime.UTC).timestamp())

        file_list = ", ".join(files) if files else ""

        md_content = f"# {title}\n\n"
        md_content += f"- **Opened**: {today} (unix: {ts})\n"
        md_content += f"- **Status**: {status}\n"
        md_content += f"- **Domain**: {domain}\n"
        if file_list:
            md_content += f"- **Files**: {file_list}\n"
        md_content += f"\n{content}\n"

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
                logger.warning(
                    "Failed to read journal index file %s: %s", index_path, e
                )
                index_data = {}
            except yaml.YAMLError as e:
                logger.warning("Failed to parse journal index %s: %s", index_path, e)
                index_data = {}
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

    @mcp.tool()
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
        record("mcp.tool.context.directory.listed", path=path or "/", count=len(entries))
        return {"entries": entries}

    @mcp.tool()
    def read_context_file(path: str) -> dict[str, str]:
        """Read any text file from the tasks/ workspace context.

        Use list_context_directory() to discover available files first.

        Args:
            path: Relative file path within tasks/ (e.g. "discoveries/index.yaml").

        Returns:
            {"content": "<file contents>", "path": "<relative path>"}
        """
        target = _safe_tasks_path(path)
        if not target.exists():
            return {"error": f"File not found: {path}"}
        if not target.is_file():
            return {"error": f"Not a file: {path}"}

        content = target.read_text(encoding="utf-8", errors="replace")
        logger.info("read_context_file: %s (%d chars)", path, len(content))
        record("mcp.tool.context.file.read", path=path, chars=len(content))
        return {"content": content, "path": path}

    @mcp.tool()
    def write_context_file(path: str, content: str) -> dict[str, str]:
        """Write a text file to the tasks/ workspace context.

        For journal entries, prefer write_journal_entry() which handles
        formatting and indexing. Use this for discoveries, lessons, specs,
        and other free-form context files.

        Allowed extensions: .md, .txt, .yaml, .yml

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
        suffix = target.suffix.lower()
        if suffix not in _ALLOWED_WRITE_SUFFIXES:
            record(
                "mcp.tool.context.file.write.failed",
                path=path,
                reason="unsupported_suffix",
                suffix=suffix,
            )
            return {
                "error": f"Unsupported format {suffix!r}. "
                f"Allowed: {', '.join(sorted(_ALLOWED_WRITE_SUFFIXES))}"
            }

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        logger.info("write_context_file: wrote %s (%d chars)", path, len(content))
        record("mcp.tool.context.file.written", path=path, chars=len(content))
        return {"status": "written", "path": path}

    @mcp.tool()
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

        Allowed extensions: .md, .txt, .yaml, .yml

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
            return cast(dict[str, str | int], _read_only_error())

        try:
            target_path = _safe_tasks_path(path)
        except ValueError as exc:
            return {"error": str(exc)}

        if target_path.suffix.lower() not in _ALLOWED_WRITE_SUFFIXES:
            return {
                "error": f"Unsupported format {target_path.suffix!r} for editing. Allowed: "
                + ", ".join(sorted(_ALLOWED_WRITE_SUFFIXES))
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

    @mcp.tool()
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
