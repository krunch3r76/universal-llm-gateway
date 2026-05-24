"""Journal entry MCP tools: list, read, and write entries under tasks/journal/.

Backed by journal/index.yaml and journal/*.md files. Enforces TASKS_READ_ONLY policy
via the tasks_path_policy helpers. All mutations record telemetry via mcp_events.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

import yaml
from mcp_events import record
from universal_logging import get_logger

from .tasks_path_policy import (
    TASKS_READ_ONLY,
    TASKS_ROOT,
    read_only_error,
    record_read_only_violation,
    safe_tasks_path,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = get_logger(__name__)


def register_journal_tools(mcp: FastMCP) -> None:
    """Register journal index/list/read/write MCP tools backed by `tasks/journal`."""

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
        index_path = TASKS_ROOT / "journal" / "index.yaml"
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
        entry_path = safe_tasks_path(f"journal/{slug}.md")
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
        if TASKS_READ_ONLY:
            record_read_only_violation(tool="write_journal_entry")
            return read_only_error()

        entry_path = safe_tasks_path(f"journal/{slug}.md")
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

        index_path = TASKS_ROOT / "journal" / "index.yaml"
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
