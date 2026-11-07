"""Offline tests for cursor tab whereami resolution."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bus_watch.cursor_tab_whereami import (
    explicit_policy_gui_host,
    resolve_cursor_tab_whereami,
)
from bus_watch.cursor_vscdb import parse_vscdb_path_env

pytestmark = pytest.mark.offline

_REAL_TID = "7689373a-2499-41f0-9ff6-fccbcf130362"


def test_whereami_unresolved_without_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "bus_watch.cursor_tab_whereami.probe_vscdb_readability",
        lambda: {
            "vscdbs_checked": ["/none.vscdb"],
            "readable_count": 0,
            "readable_paths": [],
        },
    )
    out = resolve_cursor_tab_whereami()
    assert out["basis"] == "unresolved"
    assert out["gui_host"] is None
    assert out["limitation"] == "transcript_id_required"
    assert isinstance(out["vscdbs_checked"], list)


def test_parse_vscdb_path_env_pipe_delimiter() -> None:
    rows = parse_vscdb_path_env("/tmp/a.vscdb|:8;/tmp/b.vscdb|orion-node")
    assert len(rows) == 2
    assert rows[0] == (Path("/tmp/a.vscdb"), ":8")


def test_whereami_reads_real_sqlite_unpatched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "state.vscdb"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value BLOB)")
        conn.execute(
            "INSERT INTO ItemTable VALUES (?, ?)",
            (f"glass.agentConversationPanes.{_REAL_TID}", b'{"layout":"glass"}'),
        )
    monkeypatch.setenv("CURSOR_VSCDB_PATHS", f"{db}|orion-node")
    out = resolve_cursor_tab_whereami(transcript_id=_REAL_TID)
    assert out["basis"] == "vscdb"
    assert out["gui_host"] == "orion-node"
    assert out["readable_count"] >= 1
    assert out["basis"] == "vscdb"


def test_whereami_other_glass_pane_heuristic_orion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "state.vscdb"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value BLOB)")
        conn.execute(
            "INSERT INTO ItemTable VALUES (?, ?)",
            ("glass.agentConversationPanes.other-id", b"{}"),
        )
    monkeypatch.setenv("CURSOR_VSCDB_PATHS", f"{db}|:8")
    out = resolve_cursor_tab_whereami(transcript_id=_REAL_TID)
    assert out["basis"] == "vscdb_inferred"
    assert out["inference_reason"] == "other_glass_panes"
    assert out["gui_host"] == "orion-node"
    assert out["subscription_display"] == "orion-node"


def test_explicit_policy_gui_host_requires_tick_file(tmp_path: Path) -> None:
    tick = tmp_path / "liaison-22498.tick.json"
    tick.write_text(
        '{"policy": {"gui_host": "orion-node"}}',
        encoding="utf-8",
    )
    from bus_watch import fable_lock as fl

    old = fl.WATCH_DIR
    fl.WATCH_DIR = tmp_path
    try:
        assert explicit_policy_gui_host("22498") == "orion-node"
        assert explicit_policy_gui_host("99999") is None
    finally:
        fl.WATCH_DIR = old


def test_whereami_env_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SSH_CONNECTION", "1.2.3.4 22 5.6.7.8 9")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("CURSOR_VSCDB_PATHS", raising=False)
    out = resolve_cursor_tab_whereami(
        transcript_id=_REAL_TID,
        use_process_env=True,
    )
    assert out["basis"] == "env"
    assert out["gui_host"] == "orion-node"
    assert out["ssh_client_derivation"] == "derived"
