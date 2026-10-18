"""Offline tests for cursor_io auth SQLite sync."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from cursor_bridge.cursor_auth_sync import sync_cursor_io_auth

pytestmark = pytest.mark.offline


def _make_vscdb(path: Path, rows: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value BLOB)")
        for key, value in rows.items():
            conn.execute("INSERT INTO ItemTable (key, value) VALUES (?, ?)", (key, value))
        conn.commit()


def test_sync_cursor_io_auth_copies_auth_keys(tmp_path: Path) -> None:
    source = tmp_path / "source.vscdb"
    target = tmp_path / "target.vscdb"
    _make_vscdb(
        source,
        {
            "cursorAuth/accessToken": "tok",
            "cursorAuth/refreshToken": "ref",
            "glass.lastSignedInAuthId": "auth-1",
            "unrelated": "skip",
        },
    )
    out = sync_cursor_io_auth(source_db=source, target_db=target)
    assert out["ok"] is True
    assert set(out["synced_keys"]) == {
        "cursorAuth/accessToken",
        "cursorAuth/refreshToken",
        "glass.lastSignedInAuthId",
    }
    with sqlite3.connect(target) as conn:
        keys = {
            row[0]
            for row in conn.execute("SELECT key FROM ItemTable").fetchall()
        }
    assert keys == set(out["synced_keys"])
