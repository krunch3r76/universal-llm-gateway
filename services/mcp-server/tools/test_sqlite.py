"""Tests for MCP SQLite tool database-name defaults and diagnostics."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from tools import sqlite as sqlite_tools


class _ToolRecorder:
    def __init__(self) -> None:
        self.registered: dict[str, Any] = {}

    def tool(self, **_kwargs: Any) -> Any:
        def decorator(fn: Any) -> Any:
            self.registered[fn.__name__] = fn
            return fn

        return decorator


def _create_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE facts (id INTEGER PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO facts (value) VALUES ('ok')")


def test_sql_empty_db_uses_cortex_default(tmp_path: Path, monkeypatch: Any) -> None:
    cortex_db = tmp_path / "cortex.db"
    default_db = tmp_path / "default.db"
    _create_db(cortex_db)

    monkeypatch.setattr(
        sqlite_tools,
        "_CONFIG",
        {
            "databases": {
                "cortex": str(cortex_db),
                "default": str(default_db),
            },
            "max_rows": 100,
            "allow_destructive": False,
        },
    )
    recorder = _ToolRecorder()

    sqlite_tools.register_sqlite_tools(recorder)  # type: ignore[arg-type]
    result = recorder.registered["sql"]("SELECT value FROM facts", db="")

    assert result == {"columns": ["value"], "rows": [["ok"]], "count": 1}


def test_unknown_db_error_names_cortex(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(
        sqlite_tools,
        "_CONFIG",
        {
            "databases": {"default": str(tmp_path / "default.db")},
            "max_rows": 100,
            "allow_destructive": False,
        },
    )
    recorder = _ToolRecorder()

    sqlite_tools.register_sqlite_tools(recorder)  # type: ignore[arg-type]
    result = recorder.registered["sql"]("SELECT 1", db="missing")

    assert "db='cortex'" in result["error"]
    assert "Configured databases: default" in result["error"]


def test_sqlite_execute_empty_db_requires_explicit_target(
    tmp_path: Path, monkeypatch: Any
) -> None:
    cortex_db = tmp_path / "cortex.db"
    _create_db(cortex_db)
    monkeypatch.setattr(
        sqlite_tools,
        "_CONFIG",
        {
            "databases": {"cortex": str(cortex_db)},
            "max_rows": 100,
            "allow_destructive": False,
        },
    )
    recorder = _ToolRecorder()

    sqlite_tools.register_sqlite_tools(recorder)  # type: ignore[arg-type]
    result = recorder.registered["sqlite_execute"](
        "INSERT INTO facts (value) VALUES (?)",
        db="",
        params=["nope"],
    )

    assert "requires an explicit db name" in result["error"]


def test_sqlite_execute_omitted_db_requires_explicit_target(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Omitting the ``db`` argument must also be rejected — the default is
    intentionally empty so misrouted writes never land in the wrong DB."""
    cortex_db = tmp_path / "cortex.db"
    _create_db(cortex_db)
    monkeypatch.setattr(
        sqlite_tools,
        "_CONFIG",
        {
            "databases": {"cortex": str(cortex_db)},
            "max_rows": 100,
            "allow_destructive": False,
        },
    )
    recorder = _ToolRecorder()

    sqlite_tools.register_sqlite_tools(recorder)  # type: ignore[arg-type]
    result = recorder.registered["sqlite_execute"](
        "INSERT INTO facts (value) VALUES (?)",
        params=["nope"],
    )

    assert "requires an explicit db name" in result["error"]
