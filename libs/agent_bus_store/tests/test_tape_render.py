"""Tests for tape_render CP-cell partition (I8, I9)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from agent_bus_store.tape_render import (
    _binding_for_journal,
    build_chain_segments,
    render_tape,
)

pytestmark = pytest.mark.offline

_UUID = "c3d4e5f6-a7b8-9012-cdef-123456789012"


def _verbatim_md(turn_count: int, session_id: str) -> str:
    lines = [f"# Session {session_id}\n"]
    for i in range(1, turn_count + 1):
        lines.append(f"## Turn {i} — topic-{i}")
        lines.append("### User")
        lines.append(f"User turn {i}.")
        lines.append("### Assistant")
        lines.append(f"Assistant turn {i}.")
    lines.append("\n## Session Summary\n\n**Decisions:** test\n**Open items:** None.\n")
    return "\n".join(lines)


def test_i10_tape_render_does_not_load_windows_section() -> None:
    """I10 / AC-15: tape resume payload never embeds audit ``## Windows`` concat."""
    with (
        patch("agent_bus_store.tape_render.list_checkpoint_turns", return_value=()),
        patch("cortex_store.db.cortex_conn") as mock_conn,
        patch("cortex_store.events_tape.agent_bus_tape_rendered"),
    ):
        conn = mock_conn.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = []
        result = render_tape(thread_id="6341")
    assert "## Windows" not in json.dumps(result)
    assert result["segment_count"] == 0


def test_render_tape_empty_lane() -> None:
    with (
        patch("agent_bus_store.tape_render.list_checkpoint_turns") as mock_cps,
        patch("cortex_store.db.cortex_conn") as mock_conn,
        patch("cortex_store.events_tape.agent_bus_tape_rendered"),
    ):
        mock_cps.return_value = ()
        conn = mock_conn.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = []
        result = render_tape(thread_id="100")
    assert result["thread_id"] == "100"
    assert result["segment_count"] == 0
    assert result["truncated"] is False


def test_i8_chain_segments_no_double_render(tmp_path: Path) -> None:
    """I8: S_1(1..10) → S_2(1..25) on one uuid ⇒ segments (0,10] and (10,25]."""
    files_root = tmp_path / "files"
    files_root.mkdir()
    s1 = "cursor-2026-09-07-100000-s01"
    s2 = "cursor-2026-09-07-110000-s02"
    p1 = files_root / "notes/system/transcripts" / f"{s1}.md"
    p2 = files_root / "notes/system/transcripts" / f"{s2}.md"
    p1.parent.mkdir(parents=True)
    p1.write_text(_verbatim_md(10, s1), encoding="utf-8")
    p2.write_text(_verbatim_md(25, s2), encoding="utf-8")

    journals = [
        {
            "id": 1,
            "session_id": s1,
            "conversation_uuid": _UUID,
            "prior_session_id": None,
            "file_path": f"notes/system/transcripts/{s1}.md",
            "entity_ids": ["agent-bus:6341"],
        },
        {
            "id": 2,
            "session_id": s2,
            "conversation_uuid": _UUID,
            "prior_session_id": s1,
            "file_path": f"notes/system/transcripts/{s2}.md",
            "entity_ids": ["agent-bus:6341"],
        },
    ]
    segments = build_chain_segments(journals, files_root=files_root)
    assert len(segments) == 2
    assert segments[0]["session_id"] == s1
    assert segments[0]["turn_lo"] == 0
    assert segments[0]["turn_hi"] == 10
    assert segments[0]["turn_count"] == 10
    assert segments[1]["session_id"] == s2
    assert segments[1]["turn_lo"] == 10
    assert segments[1]["turn_hi"] == 25
    assert segments[1]["turn_count"] == 15
    windows = {(s["turn_lo"], s["turn_hi"]) for s in segments}
    assert len(windows) == 2


def test_i9_binding_from_entity_ids_and_dropped_row() -> None:
    """I9: binding from entity_ids; foreign human row reported as dropped."""
    assert _binding_for_journal({"entity_ids": ["agent-bus:6341"]}, "6341") == "dominant_write"
    assert _binding_for_journal({"entity_ids": ["6341"]}, "6341") == "dominant_write"
    assert _binding_for_journal({"entity_ids": ["agent-bus:9999"]}, "6341") is None

    journals = [
        {
            "id": 1,
            "session_id": "cursor-2026-09-07-100000-x01",
            "entity_ids": ["agent-bus:6341"],
            "file_path": "notes/system/transcripts/x.md",
            "conversation_uuid": None,
        },
        {
            "id": 2,
            "session_id": "cursor-2026-09-07-100000-x02",
            "entity_ids": ["agent-bus:9999"],
            "file_path": "notes/system/transcripts/y.md",
            "conversation_uuid": None,
        },
    ]
    excluded = [
        {
            "session_id": j["session_id"],
            "reason": "dropped",
        }
        for j in journals
        if _binding_for_journal(j, "6341") is None
    ]
    assert len(excluded) == 1
    assert excluded[0]["session_id"] == "cursor-2026-09-07-100000-x02"
