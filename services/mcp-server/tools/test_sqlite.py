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


def _make_recorder_with_cortex(tmp_path: Path, monkeypatch: Any) -> _ToolRecorder:
    """Common setup: create a cortex.db with the facts table, swap _CONFIG."""
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
    return recorder


# ---------------------------------------------------------------------------
# Write-detection guard: false-positive cases that previously rejected
# legitimate read-only statements (regression coverage for the
# substring-style ``^\s*SELECT\b`` guard — see thread 1025 and
# notes/system/threads/gpt-5.5-mcp-exhaustion-investigation-2026-05-17.md).
# ---------------------------------------------------------------------------


def test_sql_accepts_leading_line_comment(tmp_path: Path, monkeypatch: Any) -> None:
    recorder = _make_recorder_with_cortex(tmp_path, monkeypatch)
    result = recorder.registered["sql"](
        "-- Partition 1: facts table\nSELECT value FROM facts"
    )
    assert result == {"columns": ["value"], "rows": [["ok"]], "count": 1}


def test_sql_accepts_leading_block_comment(tmp_path: Path, monkeypatch: Any) -> None:
    recorder = _make_recorder_with_cortex(tmp_path, monkeypatch)
    result = recorder.registered["sql"]("/* preamble */ SELECT value FROM facts")
    assert result == {"columns": ["value"], "rows": [["ok"]], "count": 1}


def test_sql_accepts_chained_leading_comments(tmp_path: Path, monkeypatch: Any) -> None:
    recorder = _make_recorder_with_cortex(tmp_path, monkeypatch)
    result = recorder.registered["sql"](
        "-- first comment\n/* second comment */\n-- third\nSELECT value FROM facts"
    )
    assert result == {"columns": ["value"], "rows": [["ok"]], "count": 1}


def test_sql_accepts_with_cte(tmp_path: Path, monkeypatch: Any) -> None:
    recorder = _make_recorder_with_cortex(tmp_path, monkeypatch)
    result = recorder.registered["sql"](
        "WITH t AS (SELECT value FROM facts) SELECT * FROM t"
    )
    assert result == {"columns": ["value"], "rows": [["ok"]], "count": 1}


def test_sql_accepts_explain(tmp_path: Path, monkeypatch: Any) -> None:
    recorder = _make_recorder_with_cortex(tmp_path, monkeypatch)
    result = recorder.registered["sql"]("EXPLAIN SELECT value FROM facts")
    # EXPLAIN returns VDBE bytecode rows; we only assert the guard accepts it.
    assert "error" not in result
    assert "rows" in result


def test_sql_accepts_values_clause(tmp_path: Path, monkeypatch: Any) -> None:
    recorder = _make_recorder_with_cortex(tmp_path, monkeypatch)
    result = recorder.registered["sql"]("VALUES (1), (2)")
    assert "error" not in result
    assert result["count"] == 2


def test_sql_accepts_select_with_keyword_in_string_literal(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A SELECT whose body contains ``UPDATE`` / ``INSERT`` inside a string
    literal or CASE expression must not trip the read-only guard.
    """
    recorder = _make_recorder_with_cortex(tmp_path, monkeypatch)
    result = recorder.registered["sql"](
        "SELECT CASE WHEN 1=1 THEN 'UPDATE me' ELSE 'INSERT here' END AS verdict"
    )
    assert result["count"] == 1
    assert result["rows"] == [["UPDATE me"]]


# ---------------------------------------------------------------------------
# Negative cases — writes still rejected by the read-only guard.
# ---------------------------------------------------------------------------


def test_sql_rejects_insert(tmp_path: Path, monkeypatch: Any) -> None:
    recorder = _make_recorder_with_cortex(tmp_path, monkeypatch)
    result = recorder.registered["sql"]("INSERT INTO facts (value) VALUES ('x')")
    assert "Only read-only statements" in result["error"]


def test_sql_rejects_update(tmp_path: Path, monkeypatch: Any) -> None:
    recorder = _make_recorder_with_cortex(tmp_path, monkeypatch)
    result = recorder.registered["sql"]("UPDATE facts SET value = 'x'")
    assert "Only read-only statements" in result["error"]


def test_sql_rejects_delete(tmp_path: Path, monkeypatch: Any) -> None:
    recorder = _make_recorder_with_cortex(tmp_path, monkeypatch)
    result = recorder.registered["sql"]("DELETE FROM facts")
    assert "Only read-only statements" in result["error"]


def test_sql_rejects_write_hidden_behind_leading_comment(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Stripping leading comments must not let an INSERT through — the
    statement-after-comments is still INSERT.
    """
    recorder = _make_recorder_with_cortex(tmp_path, monkeypatch)
    result = recorder.registered["sql"](
        "-- harmless-looking comment\nINSERT INTO facts (value) VALUES ('x')"
    )
    assert "Only read-only statements" in result["error"]


# ---------------------------------------------------------------------------
# Direct unit tests for the helper — confirm the comment-stripping +
# prefix-matching primitives behave as expected without the tool wrapper.
# ---------------------------------------------------------------------------


def test_is_read_only_sql_accepts_canonical_prefixes() -> None:
    for stmt in (
        "SELECT 1",
        "  select 1",
        "WITH t AS (SELECT 1) SELECT * FROM t",
        "EXPLAIN SELECT 1",
        "EXPLAIN QUERY PLAN SELECT 1",
        "VALUES (1), (2)",
    ):
        assert sqlite_tools._is_read_only_sql(stmt), stmt


def test_is_read_only_sql_strips_leading_comments() -> None:
    for stmt in (
        "-- one\nSELECT 1",
        "/* one */SELECT 1",
        "/* multi\nline */ SELECT 1",
        "-- a\n/* b */\n-- c\nSELECT 1",
        "-- a\nWITH t AS (SELECT 1) SELECT * FROM t",
    ):
        assert sqlite_tools._is_read_only_sql(stmt), stmt


def test_is_read_only_sql_rejects_writes_and_empty() -> None:
    for stmt in (
        "INSERT INTO t VALUES (1)",
        "UPDATE t SET x = 1",
        "DELETE FROM t",
        "DROP TABLE t",
        "PRAGMA foreign_keys = ON",
        "ATTACH DATABASE 'x' AS y",
        "-- only a comment",
        "/* only a block comment */",
        "",
        "   ",
        "-- prefix\nINSERT INTO t VALUES (1)",
        "SELECTOR INTO t VALUES (1)",  # ensure word-boundary on SELECT
    ):
        assert not sqlite_tools._is_read_only_sql(stmt), stmt
