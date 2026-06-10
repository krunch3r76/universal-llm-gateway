"""Boot continuity endpoint reconciles open_items against the resolution index."""

# ruff: noqa: F811 — pytest fixture injection reuses imported `session_env` name

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from cortex_store.routes.boot import continuity

def _ensure_workflow_state(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        cols = {
            row[1] for row in conn.execute("PRAGMA table_info(entities)").fetchall()
        }
        if "workflow_state" not in cols:
            conn.execute("ALTER TABLE entities ADD COLUMN workflow_state TEXT")
            conn.commit()
    finally:
        conn.close()


def _insert_done_todo(db_path: Path, slug: str) -> None:
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO entities (
                id, type, name, description, workflow_state, created_at, updated_at
            ) VALUES (?, 'todo', ?, ?, 'done', ?, ?)
            """,
            (f"todo:{slug}", slug, f"Closed {slug}", now, now),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_journal(
    db_path: Path,
    *,
    agent: str,
    session_id: str,
    open_items: list[str],
) -> None:
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO session_journals (
                timestamp, agent, summary, open_items, session_id
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                now,
                agent,
                "Session with reconcilable open items.",
                json.dumps(open_items),
                session_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_boot_continuity_omits_resolved_bare_todo_prefix(
    session_env: dict[str, Path],
) -> None:
    db_path = session_env["db_path"]
    _ensure_workflow_state(db_path)
    slug = "reconcile-boot"
    _insert_done_todo(db_path, slug)
    bare_item = f"todo:{slug} — next action: verify endpoint wiring"
    untouched = "Ship the unrelated follow-up"
    _insert_journal(
        db_path,
        agent="cursor",
        session_id="cursor-2026-06-01-090000-a01",
        open_items=[bare_item, untouched],
    )

    payload = continuity.get_boot_continuity(agent="cursor")
    assert payload["last_session"] is not None
    assert payload["last_session"]["open_items"] == [untouched]


def test_boot_continuity_tags_resolved_when_omit_false(
    session_env: dict[str, Path],
) -> None:
    db_path = session_env["db_path"]
    _ensure_workflow_state(db_path)
    slug = "reconcile-tag"
    _insert_done_todo(db_path, slug)
    bracketed = f"[todo:{slug}] finish the arc"
    _insert_journal(
        db_path,
        agent="cursor",
        session_id="cursor-2026-06-01-100000-a02",
        open_items=[bracketed],
    )

    payload = continuity.get_boot_continuity(agent="cursor", omit_resolved=False)
    assert payload["last_session"] is not None
    assert payload["last_session"]["open_items"] == [f"[RESOLVED] {bracketed}"]
