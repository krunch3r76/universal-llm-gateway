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
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

import yaml
from mcp_events import record

from .file_editor import perform_edit
from .local_api import _relay

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_TASKS_ROOT = Path(os.environ.get("TASKS_ROOT", "/data/tasks"))
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


def _todo_list(
    *,
    status: str,
    domain: str | None,
    context: str | None,
    priority: str | None,
    limit: int,
) -> dict[str, Any]:
    params: dict[str, str | int] = {
        k: v
        for k, v in {
            "status": status if status != "all" else None,
            "domain": domain,
            "context": context,
            "priority": priority,
            "limit": limit,
        }.items()
        if v is not None
    }

    qs = urlencode(params)
    result = _relay("cortex-api", "GET", f"/todos?{qs}")

    if "error" in result:
        return {"error": f"cortex-api error: {result['error']}"}

    items: list[dict[str, Any]] = (
        result if isinstance(result, list) else result.get("items", [])
    )  # Type hint for result from _relay should be refined to dict[str, Any] | list[dict[str, Any]]
    logger.info(
        "todo list: status=%s domain=%s context=%s → %d items",
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


def _todo_add(
    *,
    id: str,
    title: str,
    domain: str,
    context: str,
    priority: str,
    description: str,
    notes: str,
) -> dict[str, str]:
    body = {
        "id": id,
        "title": title,
        "domain": domain,
        "context": context,
        "priority": priority,
        "description": description,
        "notes": notes,
    }
    result = _relay("cortex-api", "POST", "/todos", body=body)

    if "error" in result:
        status_code = result.get("status_code")
        if status_code == 409:
            return {"error": f"todo already exists: {id}"}
        return {"error": f"cortex-api error: {result['error']}"}

    logger.info("todo add: %s (domain=%s)", id, domain)
    record(
        "mcp.tool.todo.added", id=id, domain=domain, context=context, priority=priority
    )
    return {"status": "created", "id": id}


def _todo_set_status(
    *,
    id: str,
    new_status: str,
) -> dict[str, str]:
    result = _relay("cortex-api", "PATCH", f"/todos/{id}", body={"status": new_status})

    if "error" in result:
        status_code = result.get("status_code")
        if status_code == 404:
            return {"error": f"todo not found: {id}"}
        return {"error": f"cortex-api error: {result['error']}"}

    signal = "mcp.tool.todo.done" if new_status == "done" else "mcp.tool.todo.deferred"
    logger.info("todo %s: %s", new_status, id)
    record(signal, id=id)
    return {"status": new_status, "id": id}


def register_context_tools(mcp: FastMCP) -> None:
    """Register context bridge tools on *mcp*."""

    @mcp.tool()
    def todo(
        method: str,
        status: str = "open",
        domain: str | None = None,
        context: str | None = None,
        priority: str | None = None,
        limit: int = 30,
        id: str | None = None,
        title: str | None = None,
        description: str = "",
        notes: str = "",
    ) -> dict[str, Any]:
        """List, add, complete, or defer todo items.

        Methods:
            list  — query todos by status/domain/context/priority
            add   — create a new todo (requires id, title, domain)
            done  — mark a todo as done (requires id)
            defer — mark a todo as deferred (requires id)

        Args:
            method: One of "list", "add", "done", "defer".
            status: Filter by status for list (default "open"). Use "all" for everything.
            domain: Domain filter (list) or domain tag (add).
            context: Context filter (list) or context tag (add, default "universal-llm-gateway").
            priority: Priority filter (list) or priority tag (add, default "short_term").
            limit: Maximum items for list (default 30).
            id: Todo identifier — required for add/done/defer. Must match [a-z0-9-]+.
            title: Human-readable title — required for add.
            description: Optional longer description (add only).
            notes: Optional notes (add only).

        Returns:
            list:  {"items": [...]}
            add:   {"status": "created", "id": "..."}
            done:  {"status": "done", "id": "..."}
            defer: {"status": "deferred", "id": "..."}
        """
        import re

        if method == "list":
            return _todo_list(
                status=status,
                domain=domain,
                context=context,
                priority=priority,
                limit=limit,
            )
        if method == "add":
            if _TASKS_READ_ONLY:
                _record_read_only_violation(tool="todo", operation="add")
                return _read_only_error()
            if not id or not title or not domain:
                return {"error": "add requires id, title, and domain"}
            if not re.fullmatch(r"[a-z0-9-]+", id):
                return {"error": f"id must match [a-z0-9-]+, got: {id!r}"}
            return _todo_add(
                id=id,
                title=title,
                domain=domain,
                context=context or "universal-llm-gateway",
                priority=priority or "short_term",
                description=description,
                notes=notes,
            )
        if method == "done":
            if _TASKS_READ_ONLY:
                _record_read_only_violation(tool="todo", operation="done")
                return _read_only_error()
            if not id:
                return {"error": "done requires id"}
            return _todo_set_status(id=id, new_status="done")
        if method == "defer":
            if _TASKS_READ_ONLY:
                _record_read_only_violation(tool="todo", operation="defer")
                return _read_only_error()
            if not id:
                return {"error": "defer requires id"}
            return _todo_set_status(id=id, new_status="deferred")
        # Consider extracting the read-only check into a helper function or decorator.

        return {"error": f"unknown method: {method}. Use list, add, done, defer"}

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
        record(
            "mcp.tool.context.directory.listed", path=path or "/", count=len(entries)
        )
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
            # The cast here indicates a potential type mismatch or overly broad type.
            # _read_only_error() returns dict[str, str], which is compatible with
            # dict[str, str | int], so the cast might be unnecessary or indicate
            # a deeper type issue if 'int' is truly not expected in error returns.
            return _read_only_error()  # Type checker should handle this implicitly.

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
    def context(
        op: str = "",
        path: str = "",
        content: str = "",
        target: str = "",
        line: int = 0,
        all_occurrences: bool = False,
    ) -> dict[str, Any]:
        """Unified file operations for the tasks/ workspace context directory.

        Use `context` for workspace scratchpads and durable task context:
        discoveries, lessons, specs, prompts, and similar notes under tasks/.
        ¬user uploads, dropbox files, or materials shared with the agent —
        those live in /data/files (use `files` tool).
        Prefer `project` for repository source code.

        For large markdown documents (specs, runbooks >5k chars), prefer
        the `markdown` tool which provides section-level read/write/delete
        without ingesting the entire file.

        Ops:
          read    — read file contents (path required)
          write   — create/overwrite file (path, content required)
          append  — append to end of file (path, content required)
          prepend — insert at beginning of file (path, content required)
          replace — find-and-replace in file (path, target required; content = replacement)
          insert_at_line — insert at line N (path, content, line required)
          delete  — delete a file (path required)
          list    — list directory entries (path optional, defaults to root)

        For journal entries, use dispatch(tool="write_journal_entry").
        For todos, use dispatch(tool="todo").

        Args:
            op: Operation name (see above).
            path: Relative path within tasks/, e.g. "discoveries/new-insight.md".
            content: Text content for write/edit ops (replacement text for replace).
            target: String to find — required for replace.
            line: 1-indexed line number — required for insert_at_line.
            all_occurrences: For replace: replace all matches (default false).

        Returns:
            Operation-dependent result dict.
        """
        if not op:
            raise ValueError("'op' is required")
        handlers = {
            "read": lambda: read_context_file(path)
            if path
            else _raise_value_error("'path' is required for read"),
            "write": lambda: write_context_file(path, content)
            if path and content
            else _raise_value_error("'path' and 'content' are required for write"),
            "list": lambda: list_context_directory(path),
            "append": lambda: edit_context_file(path, op, content)
            if path and content
            else _raise_value_error(f"'path' and 'content' are required for {op}"),
            "prepend": lambda: edit_context_file(path, op, content)
            if path and content
            else _raise_value_error(f"'path' and 'content' are required for {op}"),
            "replace": lambda: edit_context_file(
                path, "replace", content, target=target, all_occurrences=all_occurrences
            )
            if path and target
            else _raise_value_error("'path' and 'target' are required for replace"),
            "insert_at_line": lambda: edit_context_file(
                path, "insert_at_line", content, line=line
            )
            if path and line
            else _raise_value_error(
                "'path' and 'line' are required for insert_at_line"
            ),
            "delete": lambda: delete_context_file(path)
            if path
            else _raise_value_error("'path' is required for delete"),
        }

        if op in handlers:
            return handlers[op]()
        else:
            raise ValueError(
                f"Unknown op: {op!r}. "
                "Use: read, write, append, prepend, replace, insert_at_line, delete, list"
            )

    def _raise_value_error(msg: str) -> Any:
        raise ValueError(msg)

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
