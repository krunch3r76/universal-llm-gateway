from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from cortex_store import db
from cortex_store.dispatch_ops import ops_journals
from cortex_store.routes import session_journals


def _install_schema(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE entities (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                status TEXT,
                source_uri TEXT,
                attributes TEXT,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE assertions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id TEXT NOT NULL,
                claim TEXT,
                superseded_by INTEGER,
                review_status TEXT
            );
            CREATE TABLE relationships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_entity TEXT NOT NULL,
                to_entity TEXT NOT NULL,
                type TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE session_journals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                agent TEXT NOT NULL,
                summary TEXT NOT NULL,
                domains TEXT,
                decisions TEXT,
                open_items TEXT,
                entity_ids TEXT,
                file_path TEXT,
                session_id TEXT NOT NULL,
                prior_session_id TEXT
            );
            CREATE TABLE session_edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                agent TEXT NOT NULL,
                from_node TEXT NOT NULL,
                to_node TEXT NOT NULL,
                edge_type TEXT NOT NULL,
                strength REAL,
                edge_source TEXT,
                created_at TEXT,
                valid_until TEXT
            );
            CREATE TABLE reflective_journal (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent TEXT NOT NULL,
                register TEXT NOT NULL,
                entry TEXT NOT NULL,
                kind TEXT NOT NULL,
                session_id TEXT,
                revises INTEGER,
                consolidation_data TEXT,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            );
            CREATE TABLE journal_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_entry INTEGER NOT NULL,
                to_entry INTEGER,
                to_entity TEXT,
                link_type TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def session_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    db_path = tmp_path / "cortex.db"
    files_root = tmp_path / "files"
    files_root.mkdir(parents=True)
    _install_schema(db_path)
    monkeypatch.setattr(db, "_CORTEX_DB", db_path)
    monkeypatch.setattr(ops_journals, "_FILES_ROOT", files_root)
    return {"db_path": db_path, "files_root": files_root}


def _transcript(summary: str) -> str:
    return (
        f"# Session Transcript\n\n"
        f"## Session Summary\n{summary}\n\n"
        "## Turn 1\n"
        "### User\nPlease continue the handoff capture arc and preserve atomicity.\n\n"
        "### Assistant\nI audited the write path, confirmed the transaction boundary, and mapped the rollback risks.\n\n"
        "## Turn 2\n"
        "### User\nMake sure the next session can resume without reconstructing context from scratch.\n\n"
        "### Assistant\nI will persist a continuation-grade handoff prompt, verify the link direction, and keep the summary grounded in the completed work.\n"
    )


def _payload(
    *,
    session_id: str,
    agent: str = "orion",
    prior_session_id: str | None = None,
    handoff_prompt: str | None = None,
) -> dict[str, Any]:
    summary = "Validated the session-close handoff capture path and checked rollback behavior."
    return {
        "session_id": session_id,
        "agent": agent,
        "transcript_md": _transcript(summary),
        "summary": summary,
        "domains": ["cortex"],
        "decisions": ["Persist handoff prompts as reflective journal entries."],
        "open_items": ["Finish the docstring and test pass."],
        "entity_ids": ["service:cortex"],
        "prior_session_id": prior_session_id,
        "handoff_prompt": handoff_prompt,
    }


def _query_one(
    db_path: Path, sql: str, params: tuple[Any, ...] = ()
) -> dict[str, Any] | None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _query_count(db_path: Path, sql: str, params: tuple[Any, ...] = ()) -> int:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(sql, params).fetchone()
        assert row is not None
        return int(row[0])
    finally:
        conn.close()


def test_session_close_happy_path_with_handoff(session_env: dict[str, Path]) -> None:
    db_path = session_env["db_path"]
    files_root = session_env["files_root"]
    handoff = "Start with the openapi and tool-doc pass, then verify rollback tests."

    result = ops_journals._op_session_close(
        **_payload(
            session_id="orion-2026-05-04-0844",
            prior_session_id="orion-2026-05-04-0700",
            handoff_prompt=handoff,
        )
    )

    assert result["handoff_entry_id"] is not None
    assert result["transcript_entity_id"] == "transcript:orion-2026-05-04-0844"
    assert (files_root / result["transcript_path"]).is_file()

    journal = _query_one(
        db_path,
        "SELECT * FROM session_journals WHERE session_id = ?",
        ("orion-2026-05-04-0844",),
    )
    assert journal is not None

    edge = _query_one(
        db_path,
        "SELECT * FROM session_edges WHERE from_node = ? AND to_node = ? AND edge_type = 'continues'",
        (
            "transcript:orion-2026-05-04-0844",
            "transcript:orion-2026-05-04-0700",
        ),
    )
    assert edge is not None

    entry = _query_one(
        db_path,
        "SELECT * FROM reflective_journal WHERE id = ?",
        (result["handoff_entry_id"],),
    )
    assert entry is not None
    assert entry["kind"] == "handoff"
    assert entry["register"] == "self"
    assert entry["entry"] == handoff

    link = _query_one(
        db_path,
        "SELECT * FROM journal_links WHERE from_entry = ? AND link_type = 'handoff_for'",
        (result["handoff_entry_id"],),
    )
    assert link is not None
    assert link["to_entity"] == "transcript:orion-2026-05-04-0844"


def test_session_close_without_handoff_is_clean_no_warnings(
    session_env: dict[str, Path],
) -> None:
    """Per assertion 8384: handoff absence is not a gap — no post-close warning."""
    db_path = session_env["db_path"]

    result = ops_journals._op_session_close(
        **_payload(session_id="cursor-2026-05-04-0844", agent="cursor")
    )

    assert result["handoff_entry_id"] is None
    warning = result.get("_warning", {})
    findings = warning.get("post_close_findings", [])
    assert not any(f["kind"] == "missing_handoff" for f in findings)
    assert _query_count(db_path, "SELECT COUNT(*) FROM reflective_journal") == 0
    assert _query_count(db_path, "SELECT COUNT(*) FROM journal_links") == 0


def test_session_close_rolls_back_and_unlinks_transcript_on_handoff_insert_failure(
    session_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = session_env["db_path"]
    files_root = session_env["files_root"]

    def _boom(*_args: Any, **_kwargs: Any) -> int:
        raise RuntimeError("handoff insert failed")

    monkeypatch.setattr(session_journals, "_insert_reflective_entry_tx", _boom)

    result = ops_journals._op_session_close(
        **_payload(
            session_id="orion-2026-05-04-0845",
            prior_session_id="orion-2026-05-04-0700",
            handoff_prompt="Resume with rollback verification.",
        )
    )

    assert "Session close failed after transcript write" in result["error"]
    assert not (
        files_root / "notes/system/transcripts/orion-2026-05-04-0845.md"
    ).exists()
    assert _query_count(db_path, "SELECT COUNT(*) FROM entities") == 0
    assert _query_count(db_path, "SELECT COUNT(*) FROM session_journals") == 0
    assert _query_count(db_path, "SELECT COUNT(*) FROM session_edges") == 0
    assert _query_count(db_path, "SELECT COUNT(*) FROM reflective_journal") == 0
    assert _query_count(db_path, "SELECT COUNT(*) FROM journal_links") == 0


def test_session_close_rolls_back_and_unlinks_transcript_on_link_failure(
    session_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = session_env["db_path"]
    files_root = session_env["files_root"]

    def _boom(*_args: Any, **_kwargs: Any) -> int:
        raise RuntimeError("link insert failed")

    monkeypatch.setattr(session_journals, "_insert_journal_link_tx", _boom)

    result = ops_journals._op_session_close(
        **_payload(
            session_id="orion-2026-05-04-0846",
            prior_session_id="orion-2026-05-04-0700",
            handoff_prompt="Resume by checking the journal link direction.",
        )
    )

    assert "Session close failed after transcript write" in result["error"]
    assert not (
        files_root / "notes/system/transcripts/orion-2026-05-04-0846.md"
    ).exists()
    assert _query_count(db_path, "SELECT COUNT(*) FROM entities") == 0
    assert _query_count(db_path, "SELECT COUNT(*) FROM session_journals") == 0
    assert _query_count(db_path, "SELECT COUNT(*) FROM session_edges") == 0
    assert _query_count(db_path, "SELECT COUNT(*) FROM reflective_journal") == 0
    assert _query_count(db_path, "SELECT COUNT(*) FROM journal_links") == 0


def test_session_close_warns_when_prior_session_id_is_omitted(
    session_env: dict[str, Path],
) -> None:
    first = ops_journals._op_session_close(
        **_payload(
            session_id="orion-2026-05-04-0700",
            handoff_prompt="Next session should continue the handoff capture work.",
        )
    )
    assert first["handoff_entry_id"] is not None

    second = ops_journals._op_session_close(
        **_payload(
            session_id="orion-2026-05-04-0847",
            handoff_prompt="Resume with the final documentation pass.",
        )
    )

    findings = second.get("_warning", {}).get("post_close_findings", [])
    assert any(f["kind"] == "prior_session_id_omitted" for f in findings)
