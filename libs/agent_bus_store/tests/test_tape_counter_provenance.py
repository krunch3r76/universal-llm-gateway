"""Regression tests for tape counter provenance (a:34597 legs 1–2)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from agent_bus_store.tape_harvest import render_tape_with_harvest
from agent_bus_store.tape_membership import build_lane_segments
from agent_bus_store.tape_pour import _excluded_counts_projection
from agent_bus_store.tape_render import segment_codec_counts

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


def test_segment_codec_counts_messages_v1_nonzero() -> None:
    """AC-3b: codec_used on segment drives segment_codec_counts."""
    segments = [
        {"from_sealed": True, "codec_used": "messages-v1"},
        {"from_sealed": True, "codec_used": "md-v1"},
    ]
    counts = segment_codec_counts(segments)
    assert counts["messages-v1"] == 1
    assert counts["md-v1"] == 1


def test_build_lane_segments_propagates_verbatim_codec(tmp_path: Path) -> None:
    """AC-1a authority: build_chain_segments copies journal verbatim_codec onto segment."""
    files_root = tmp_path / "files"
    files_root.mkdir()
    sid = "cursor-2026-09-07-100000-mv1"
    rel_path = f"notes/system/transcripts/{sid}.md"
    path = files_root / rel_path
    path.parent.mkdir(parents=True)
    path.write_text(_verbatim_md(2, sid), encoding="utf-8")

    lane_journals = [
        {
            "session_id": sid,
            "conversation_uuid": _UUID,
            "prior_session_id": None,
            "file_path": rel_path,
            "entity_ids": ["agent-bus:6341"],
            "verbatim_codec": "messages-v1",
            "_binding": "dominant_write",
            "_dominant_lane": "6341",
        }
    ]
    with patch("agent_bus_store.tape_render.post_lid_tail", return_value=(0, None)):
        segments = build_lane_segments(
            lane_journals=lane_journals,
            files_root=files_root,
            excluded=[],
        )
    assert segments[0]["verbatim_codec"] == "messages-v1"
    assert segment_codec_counts(segments)["messages-v1"] == 1


def test_resume_door_excluded_counts_scope_and_no_dead_keys() -> None:
    """AC-2a/2c: resume door omits discover-only keys and names scope."""
    excluded = [
        {"session_id": "s1", "reason": "read_only"},
        {"session_id": "s2", "reason": "dropped"},
    ]
    counts, scope_meta = _excluded_counts_projection(excluded, harvest_stats=None)
    assert "foreign_dominant" not in counts
    assert "no_touch" not in counts
    assert counts["read_only"] == 1
    assert counts["dropped"] == 1
    assert scope_meta == {
        "scope": "post_cite_prefilter_binding_failures",
        "door": "resume_fence",
    }


def test_render_tape_resume_path_open_line_excluded_counts(tmp_path: Path) -> None:
    """AC-2c: render with harvest=False exposes scope and no dead excluded keys."""
    files_root = tmp_path / "files"
    files_root.mkdir()
    sid = "cursor-2026-09-07-100000-lane"
    rel_path = f"notes/system/transcripts/{sid}.md"
    path = files_root / rel_path
    path.parent.mkdir(parents=True)
    path.write_text(_verbatim_md(1, sid), encoding="utf-8")

    journal_row = {
        "id": 1,
        "session_id": sid,
        "conversation_uuid": _UUID,
        "prior_session_id": None,
        "file_path": rel_path,
        "entity_ids": json.dumps(["agent-bus:6341"]),
        "closed_by": "cursor",
        "verbatim_codec": "md-v1",
    }

    with (
        patch("agent_bus_store.tape_membership.list_checkpoint_turns", return_value=()),
        patch("agent_bus_store.tape_cells.list_checkpoint_turns", return_value=()),
        patch("cortex_store.db.cortex_conn") as mock_conn,
        patch("cortex_store.events_tape.agent_bus_tape_rendered"),
        patch("cortex_store.events_tape.transcript_legacy_md_read"),
        patch("cortex_store.dispatch_ops._shared._FILES_ROOT", files_root),
    ):
        conn = mock_conn.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = [journal_row]
        result = render_tape_with_harvest(
            thread_id="6341",
            budget_bytes=65536,
            harvest=False,
        )

    open_line = result["open_line"]
    excluded = open_line["excluded_counts"]
    assert "foreign_dominant" not in excluded
    assert "no_touch" not in excluded
    assert open_line["excluded_counts_scope"] == {
        "scope": "post_cite_prefilter_binding_failures",
        "door": "resume_fence",
    }
    assert open_line["harvest"] is None
