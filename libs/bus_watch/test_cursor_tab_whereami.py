"""Offline tests for cursor tab whereami resolution."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from bus_watch.cursor_tab_whereami import resolve_cursor_tab_whereami

pytestmark = pytest.mark.offline

_TID = "7689373a-1111-4111-8111-111111111111"


def test_whereami_unresolved_without_transcript() -> None:
    out = resolve_cursor_tab_whereami()
    assert out["basis"] == "unresolved"
    assert out["gui_host"] is None
    assert out["subscription_display"] is None


def test_whereami_vscdb_orion_pane(tmp_path: Path) -> None:
    db = tmp_path / "cursor-orion" / "state.vscdb"
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value BLOB)")
        conn.execute(
            "INSERT INTO ItemTable VALUES (?, ?)",
            (f"glass.agentConversationPanes.{_TID}", b'{"layout":"glass"}'),
        )

    with patch(
        "bus_watch.cursor_tab_whereami._all_readable_vscdbs",
        return_value=[db],
    ):
        out = resolve_cursor_tab_whereami(transcript_id=_TID)

    assert out["basis"] == "vscdb"
    assert out["gui_host"] == "orion-node"
    assert out["subscription_display"] == "orion-node"
    assert out["ssh_client"] is True
    assert out["layout"] == "glass"


def test_whereami_env_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SSH_CONNECTION", "1.2.3.4 22 5.6.7.8 9")
    monkeypatch.delenv("DISPLAY", raising=False)
    out = resolve_cursor_tab_whereami(
        transcript_id=_TID,
        use_process_env=True,
    )
    assert out["basis"] == "env"
    assert out["gui_host"] == "orion-node"
