#!/usr/bin/env python3
"""Seed the SQLite context database with MCP tool definitions.

Usage:
    python scripts/seed-context-db.py
    python scripts/seed-context-db.py --db ~/mcp-data/databases/context.db
    python scripts/seed-context-db.py --config ~/mcp-data/sqlite.yaml

Idempotent - uses INSERT OR REPLACE, safe to re-run.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import yaml

SEED_SCHEMA = """\
CREATE TABLE IF NOT EXISTS mcp_tools (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL,
    description TEXT,
    parameters TEXT,
    when_to_use TEXT,
    when_not_to_use TEXT,
    gotchas TEXT,
    sandbox TEXT
);

CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    role TEXT,
    relationship TEXT,
    notes TEXT,
    active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    source TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS deadlines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL,
    date TEXT NOT NULL,
    category TEXT,
    urgency TEXT,
    notes TEXT,
    resolved INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT,
    lesson TEXT NOT NULL,
    context TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
"""

MCP_TOOLS = [
    {
        "name": "list_files",
        "category": "filesystem",
        "description": "List files recursively under the sandbox (~/mcp-data/files)",
        "parameters": '{"directory": "string, optional"}',
        "when_to_use": "Need to see what files exist in the sandbox",
        "when_not_to_use": "Looking for project source files (use list_project_files)",
        "gotchas": "Paths are relative to sandbox root",
        "sandbox": "mcp_files",
    },
    {
        "name": "read_file",
        "category": "filesystem",
        "description": "Read a file - plain text (.md, .txt), Word (.docx), or OpenDocument (.odt)",
        "parameters": '{"path": "string, required"}',
        "when_to_use": "Reading documents, notes, or exports from the sandbox",
        "when_not_to_use": "Reading project source files (use read_project_file)",
        "gotchas": "Only supports .md, .txt, .docx, .odt formats",
        "sandbox": "mcp_files",
    },
    {
        "name": "write_file",
        "category": "filesystem",
        "description": "Write .md, .txt, .docx, or .pdf to the sandbox",
        "parameters": '{"path": "string, required", "content": "string, required"}',
        "when_to_use": "Saving drafts, exports, documents for the user",
        "when_not_to_use": "Writing project source code",
        "gotchas": "Auto-generates binary formats (docx, pdf) from text content",
        "sandbox": "mcp_files",
    },
    {
        "name": "edit_file",
        "category": "filesystem",
        "description": "Atomic edit: prepend, append, insert_at_line, replace",
        "parameters": '{"path": "string", "operation": "string", "content": "string", "line": "int, optional", "target": "string, optional", "all_occurrences": "bool"}',
        "when_to_use": "Modifying a file without reading it first",
        "when_not_to_use": "Full file rewrites (use write_file)",
        "gotchas": "Only .md and .txt; replace returns replacements_made count",
        "sandbox": "mcp_files",
    },
    {
        "name": "delete_file",
        "category": "filesystem",
        "description": "Delete a file (directories rejected)",
        "parameters": '{"path": "string, required"}',
        "when_to_use": "Removing an obsolete file from the sandbox",
        "when_not_to_use": "Deleting project source files",
        "gotchas": "Directories cannot be deleted",
        "sandbox": "mcp_files",
    },
    {
        "name": "list_project_files",
        "category": "project",
        "description": "List git-tracked text files in the project",
        "parameters": '{"directory": "string, optional", "max_depth": "int, default 3"}',
        "when_to_use": "Exploring codebase structure",
        "when_not_to_use": "Looking for sandbox files (use list_files)",
        "gotchas": "Only git-tracked files; binary files excluded",
        "sandbox": "n/a",
    },
    {
        "name": "read_project_file",
        "category": "project",
        "description": "Read any tracked text file from the project",
        "parameters": '{"path": "string, required"}',
        "when_to_use": "Reading source code, config, documentation",
        "when_not_to_use": "Reading sandbox files (use read_file)",
        "gotchas": "Binary files rejected; read-only",
        "sandbox": "n/a",
    },
    {
        "name": "search_project_files",
        "category": "project",
        "description": "Regex search across tracked files",
        "parameters": '{"pattern": "string", "directory": "string, optional", "max_results": "int, default 50"}',
        "when_to_use": "Finding code patterns, function definitions, usages",
        "when_not_to_use": "Searching sandbox files",
        "gotchas": "Case-sensitive regex; binary files skipped",
        "sandbox": "n/a",
    },
    {
        "name": "web_search",
        "category": "web",
        "description": "Internet search via Brave Search API",
        "parameters": '{"query": "string", "max_results": "int, 1-20, default 5"}',
        "when_to_use": "Finding current information, URLs, documentation",
        "when_not_to_use": "Content is available locally",
        "gotchas": "Requires BRAVE_SEARCH_API_KEY; returns snippets not full content",
        "sandbox": "n/a",
    },
    {
        "name": "web_fetch",
        "category": "web",
        "description": "Fetch + extract clean text from a URL",
        "parameters": '{"url": "string", "max_chars": "int, default 36000", "start_offset": "int, default 0"}',
        "when_to_use": "Reading public web pages, documentation, articles",
        "when_not_to_use": "Authenticated pages (use browser tools); private/loopback URLs blocked",
        "gotchas": "No JS rendering; use start_offset for pagination if truncated",
        "sandbox": "n/a",
    },
    {
        "name": "rag",
        "category": "rag",
        "description": "RAG knowledge retrieval and index management by op name",
        "parameters": '{"op": "search", "arguments": {"query": "string", "top_k": "int, optional", "scope": "string, optional"}}',
        "when_to_use": "Finding relevant context by meaning, not exact text",
        "when_not_to_use": "Searching for exact strings (use search_project_files)",
        "gotchas": "Requires RAG service running; returns distance scores",
        "sandbox": "n/a",
    },
    {
        "name": "list_todos",
        "category": "context",
        "description": "Query todos.db for work items (status, domain, context, priority filters)",
        "parameters": '{"status": "string, default open", "domain": "string, optional", "limit": "int, default 20"}',
        "when_to_use": "Checking current priorities and open work items",
        "when_not_to_use": "N/A",
        "gotchas": "Returns structured items, not the full YAML",
        "sandbox": "n/a",
    },
    {
        "name": "list_journal_entries",
        "category": "context",
        "description": "Browse journal entry index",
        "parameters": '{"limit": "int, default 20", "domain": "string, optional"}',
        "when_to_use": "Finding past investigations and decisions",
        "when_not_to_use": "N/A",
        "gotchas": "Returns metadata only; use read_journal_entry for full content",
        "sandbox": "n/a",
    },
    {
        "name": "read_journal_entry",
        "category": "context",
        "description": "Read full journal entry by slug",
        "parameters": '{"slug": "string, required"}',
        "when_to_use": "Deep-reading a specific investigation or decision log",
        "when_not_to_use": "N/A",
        "gotchas": "Slug must match an existing entry",
        "sandbox": "n/a",
    },
    {
        "name": "write_journal_entry",
        "category": "context",
        "description": "Create journal entry with auto-indexing",
        "parameters": '{"slug": "string", "title": "string", "summary": "string", "domain": "string", "status": "string", "files": "list, optional", "content": "string"}',
        "when_to_use": "Recording investigation findings, architectural decisions",
        "when_not_to_use": "Quick notes (use write_context_file)",
        "gotchas": "slug must be unique",
        "sandbox": "n/a",
    },
    {
        "name": "browser_navigate",
        "category": "browser",
        "description": "Navigate to a URL using authenticated Firefox session",
        "parameters": '{"url": "string, required"}',
        "when_to_use": "Accessing authenticated pages, JS-rendered SPAs",
        "when_not_to_use": "Public static pages (use web_fetch)",
        "gotchas": "Uses host Firefox cookies; 15s timeout for domcontentloaded",
        "sandbox": "n/a",
    },
    {
        "name": "browser_get_content",
        "category": "browser",
        "description": "Extract visible text from current page (up to 100k chars)",
        "parameters": "{}",
        "when_to_use": "Reading page content after navigation",
        "when_not_to_use": "Need to see visual layout (use browser_screenshot)",
        "gotchas": "Text only; truncated at 100k chars",
        "sandbox": "n/a",
    },
    {
        "name": "list_clips",
        "category": "clips",
        "description": "List saved web clips (most recent first)",
        "parameters": '{"limit": "int, default 20"}',
        "when_to_use": "Finding user-clipped web content",
        "when_not_to_use": "Can navigate to the page directly (use browser tools)",
        "gotchas": "Returns metadata only; use read_clip for full content",
        "sandbox": "mcp_files",
    },
    {
        "name": "read_clip",
        "category": "clips",
        "description": "Read full clip content by filename",
        "parameters": '{"clip_id": "string, required"}',
        "when_to_use": "Reading a specific user-clipped page",
        "when_not_to_use": "N/A",
        "gotchas": "Clip ID is the filename from list_clips",
        "sandbox": "mcp_files",
    },
    {
        "name": "sql",
        "category": "sqlite",
        "description": "Execute a read-only SELECT against a SQLite database",
        "parameters": '{"sql": "string, required", "db": "string, default default", "params": "list, optional"}',
        "when_to_use": "Fetching structured context data (tools, facts, people, deadlines)",
        "when_not_to_use": "Write operations (use sqlite_execute)",
        "gotchas": "SELECT only; row limit enforced (default 100); use parameterized queries",
        "sandbox": "n/a",
    },
    {
        "name": "sqlite_execute",
        "category": "sqlite",
        "description": "Execute a write statement (INSERT, UPDATE, DELETE, CREATE TABLE)",
        "parameters": '{"sql": "string, required", "db": "string, default default", "params": "list, optional"}',
        "when_to_use": "Writing structured data, creating tables",
        "when_not_to_use": "Read queries (use sql); DROP/PRAGMA blocked by default",
        "gotchas": "Destructive statements blocked unless allow_destructive is true",
        "sandbox": "n/a",
    },
    {
        "name": "sqlite_schema",
        "category": "sqlite",
        "description": "Introspect database tables and columns",
        "parameters": '{"db": "string, default default", "table": "string, optional"}',
        "when_to_use": "Discovering available tables and column types before querying",
        "when_not_to_use": "N/A",
        "gotchas": "Returns column types, pk, nullable info",
        "sandbox": "n/a",
    },
    {
        "name": "sqlite_list_databases",
        "category": "sqlite",
        "description": "List all configured SQLite databases with paths and descriptions",
        "parameters": "{}",
        "when_to_use": "Discovering which databases are available",
        "when_not_to_use": "N/A",
        "gotchas": "Databases are configured in sqlite.yaml on the host",
        "sandbox": "n/a",
    },
]


class SeedArgs(argparse.Namespace):
    """Typed argparse namespace for seed script flags."""

    db: str | None = None
    config: str = os.path.expanduser("~/mcp-data/sqlite.yaml")


def _as_mapping(value: object) -> Mapping[str, object]:
    """Return *value* as a string-key mapping or empty mapping."""
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        mapping_value = cast(Mapping[object, object], value)
        for key, item in mapping_value.items():
            if isinstance(key, str):
                normalized[key] = item
        return normalized
    return {}


def _load_yaml_object(path: Path) -> object:
    """Load YAML from *path* and return a generic object."""
    return cast(object, yaml.safe_load(path.read_text(encoding="utf-8")))


def resolve_db_path(config_path: str | None, db_flag: str | None) -> Path:
    """Determine the database path from --db flag or config file."""
    if db_flag:
        return Path(os.path.expanduser(db_flag))

    if config_path and Path(config_path).exists():
        try:
            loaded = _load_yaml_object(Path(config_path))
            raw = _as_mapping(loaded)
            sqlite_section = _as_mapping(raw.get("sqlite", raw))
            databases = _as_mapping(sqlite_section.get("databases", {}))
            default_entry = databases.get("default", {})
            if isinstance(default_entry, str):
                return Path(os.path.expanduser(default_entry))
            default_entry_map = _as_mapping(default_entry)
            path = default_entry_map.get("path", "")
            if isinstance(path, str) and path:
                return Path(os.path.expanduser(path))
        except (OSError, yaml.YAMLError) as exc:
            print(
                f"Warning: failed to read config {config_path}: {exc}", file=sys.stderr
            )

    print("Error: no database path found. Use --db or --config.", file=sys.stderr)
    sys.exit(1)


def seed_schema(conn: sqlite3.Connection) -> None:
    """Create all tables if they don't exist."""
    cursor = conn.executescript(SEED_SCHEMA)
    cursor.close()
    print("  Schema ensured (CREATE TABLE IF NOT EXISTS)")


def seed_mcp_tools(conn: sqlite3.Connection) -> None:
    """Insert or replace MCP tool definitions."""
    for tool in MCP_TOOLS:
        cursor = conn.execute(
            """INSERT OR REPLACE INTO mcp_tools
               (name, category, description, parameters, when_to_use,
                when_not_to_use, gotchas, sandbox)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                tool["name"],
                tool["category"],
                tool["description"],
                tool["parameters"],
                tool["when_to_use"],
                tool["when_not_to_use"],
                tool["gotchas"],
                tool["sandbox"],
            ),
        )
        cursor.close()
    conn.commit()
    print(f"  Seeded {len(MCP_TOOLS)} MCP tool definitions")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the SQLite context database")
    _db_action = parser.add_argument(
        "--db",
        help="Database file path (overrides config lookup)",
    )
    _config_action = parser.add_argument(
        "--config",
        default=os.path.expanduser("~/mcp-data/sqlite.yaml"),
        help="Path to sqlite.yaml config (default: ~/mcp-data/sqlite.yaml)",
    )
    args = parser.parse_args(namespace=SeedArgs())

    db_path = resolve_db_path(args.config, args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Seeding database: {db_path}")

    with sqlite3.connect(str(db_path)) as conn:
        pragma_cursor = conn.execute("PRAGMA journal_mode=WAL")
        pragma_cursor.close()
        seed_schema(conn)
        seed_mcp_tools(conn)

    print("Done.")


if __name__ == "__main__":
    main()
