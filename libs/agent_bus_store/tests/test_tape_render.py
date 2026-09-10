"""Tests for tape_render CP-cell partition (I8, I9)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from agent_bus_store.tape_render import (
    _binding_for_journal,
    _cells_for_lane,
    build_chain_segments,
    render_tape,
)
from agent_bus_store.checkpoint_windows_render import CheckpointTurnRow

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
        patch("agent_bus_store.tape_membership.list_checkpoint_turns", return_value=()),
        patch("agent_bus_store.tape_pour.list_checkpoint_turns", return_value=()),
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
        patch("agent_bus_store.tape_membership.list_checkpoint_turns", return_value=()),
        patch("agent_bus_store.tape_pour.list_checkpoint_turns", return_value=()),
        patch("cortex_store.db.cortex_conn") as mock_conn,
        patch("cortex_store.events_tape.agent_bus_tape_rendered"),
    ):
        conn = mock_conn.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = []
        result = render_tape(thread_id="100")
    assert result["open_line"]["thread_id"] == "100"
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
    """I9: four-valued binding + dominant_lane; foreign rows dropped or read_only."""
    assert _binding_for_journal(
        {"entity_ids": ["agent-bus:6341"], "dominant_lane": "6341"},
        "6341",
    ) == ("dominant_write", "6341")
    assert _binding_for_journal(
        {"entity_ids": ["6341"], "dominant_lane": "6341"},
        "6341",
    ) == ("dominant_write", "6341")
    assert _binding_for_journal(
        {
            "entity_ids": [],
            "conversation_uuid": _UUID,
            "dominant_lane": "6341",
        },
        "6341",
        explicit_uuids={_UUID},
    ) == ("explicit_cp", "6341")
    assert _binding_for_journal(
        {
            "entity_ids": [],
            "conversation_uuid": _UUID,
            "dominant_lane": "6341",
            "closed_by": "succession",
        },
        "6341",
    ) == ("dominant_write", "6341")
    assert _binding_for_journal(
        {"entity_ids": [], "closed_by": "succession"},
        "6341",
    ) == ("sole", "6341")
    assert _binding_for_journal(
        {"entity_ids": ["agent-bus:9999"], "dominant_lane": "9999"},
        "6341",
    ) == ("read_only", "9999")
    assert _binding_for_journal({"entity_ids": ["agent-bus:9999"]}, "6341") == (None, None)

    journals = [
        {
            "id": 1,
            "session_id": "cursor-2026-09-07-100000-x01",
            "entity_ids": ["agent-bus:6341"],
            "file_path": "notes/system/transcripts/x.md",
            "conversation_uuid": None,
            "dominant_lane": "6341",
        },
        {
            "id": 2,
            "session_id": "cursor-2026-09-07-100000-x02",
            "entity_ids": ["agent-bus:9999"],
            "file_path": "notes/system/transcripts/y.md",
            "conversation_uuid": None,
            "dominant_lane": "9999",
        },
    ]
    excluded = [
        {
            "session_id": j["session_id"],
            "reason": "dropped",
        }
        for j in journals
        if _binding_for_journal(j, "6341")[0] is None
    ]
    read_only = [
        j["session_id"]
        for j in journals
        if _binding_for_journal(j, "6341")[0] == "read_only"
    ]
    assert len(excluded) == 0
    assert read_only == ["cursor-2026-09-07-100000-x02"]


def test_render_tape_messages_contain_user_and_assistant_speech(tmp_path: Path) -> None:
    """AC-9/18: messages payload carries speech bodies, not heading lines."""
    files_root = tmp_path / "files"
    files_root.mkdir()
    sid = "cursor-2026-09-07-100000-sp1"
    rel_path = f"notes/system/transcripts/{sid}.md"
    path = files_root / rel_path
    path.parent.mkdir(parents=True)
    path.write_text(_verbatim_md(2, sid), encoding="utf-8")

    journal_row = {
        "id": 1,
        "session_id": sid,
        "conversation_uuid": _UUID,
        "prior_session_id": None,
        "file_path": rel_path,
        "entity_ids": json.dumps(["agent-bus:6341"]),
        "closed_by": "cursor",
    }

    with (
        patch("agent_bus_store.tape_membership.list_checkpoint_turns", return_value=()),
        patch("agent_bus_store.tape_pour.list_checkpoint_turns", return_value=()),
        patch("cortex_store.db.cortex_conn") as mock_conn,
        patch("cortex_store.events_tape.agent_bus_tape_rendered"),
        patch("cortex_store.events_tape.transcript_legacy_md_read"),
        patch("cortex_store.dispatch_ops._shared._FILES_ROOT", files_root),
    ):
        conn = mock_conn.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = [journal_row]
        result = render_tape(thread_id="6341", include_extras=True)

    assert result["segment_count"] == 1
    msgs = result["messages"]
    assert len(msgs) == 4
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "User turn 1."
    assert msgs[0]["turn_index"] == 1
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"] == "Assistant turn 1."
    assert msgs[2]["content"] == "User turn 2."
    assert msgs[3]["content"] == "Assistant turn 2."
    assert not any(m["content"].startswith("### ") for m in msgs)
    assert len(result["excluded"]) == 0


def test_i9_render_tape_reports_dropped_human_row(tmp_path: Path) -> None:
    """I9: after binding filter, dropped human rows appear in excluded."""
    files_root = tmp_path / "files"
    files_root.mkdir()
    lane_sid = "cursor-2026-09-07-100000-lane"
    foreign_sid = "cursor-2026-09-07-100000-foreign"
    for sid, eids in (
        (lane_sid, ["agent-bus:6341"]),
        (foreign_sid, ["agent-bus:9999"]),
    ):
        rel = f"notes/system/transcripts/{sid}.md"
        path = files_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_verbatim_md(1, sid), encoding="utf-8")

    rows = [
        {
            "id": 1,
            "session_id": lane_sid,
            "conversation_uuid": _UUID,
            "prior_session_id": None,
            "file_path": f"notes/system/transcripts/{lane_sid}.md",
            "entity_ids": json.dumps(["agent-bus:6341"]),
            "closed_by": "cursor",
        },
        {
            "id": 2,
            "session_id": foreign_sid,
            "conversation_uuid": None,
            "prior_session_id": None,
            "file_path": f"notes/system/transcripts/{foreign_sid}.md",
            "entity_ids": json.dumps(["agent-bus:9999"]),
            "closed_by": "cursor",
            "dominant_lane": "9999",
        },
    ]

    with (
        patch("agent_bus_store.tape_membership.list_checkpoint_turns", return_value=()),
        patch("agent_bus_store.tape_pour.list_checkpoint_turns", return_value=()),
        patch("cortex_store.db.cortex_conn") as mock_conn,
        patch("cortex_store.events_tape.agent_bus_tape_rendered"),
        patch("cortex_store.events_tape.transcript_legacy_md_read"),
        patch("cortex_store.dispatch_ops._shared._FILES_ROOT", files_root),
    ):
        conn = mock_conn.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = rows
        result = render_tape(thread_id="6341")

    dropped = [e for e in result["excluded"] if e.get("reason") == "dropped"]
    read_only = [e for e in result["excluded"] if e.get("reason") == "read_only"]
    assert len(dropped) == 0
    assert len(read_only) == 1
    assert read_only[0]["session_id"] == foreign_sid
    assert result["messages"][0]["content"] == "User turn 1."


def test_b8_cells_join_chain_segments_and_bus_turn_id(tmp_path: Path) -> None:
    """B-8 / AC-14: two CPs over one uuid chain join turn ranges + closing bus_turn_id."""
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

    cp1 = CheckpointTurnRow(turn_number=5, cp_ordinal=1, created_at="", subject="CHECKPOINT")
    cp2 = CheckpointTurnRow(turn_number=12, cp_ordinal=2, created_at="", subject="CHECKPOINT")
    cp_bodies = {
        5: f"Window: transcript_id={_UUID} · turns@cp=10\n",
        12: f"Window: transcript_id={_UUID} · turns@cp=15\n",
    }

    class _Result:
        def __init__(self, turn_number: int) -> None:
            self._turn_number = turn_number

        def fetchone(self) -> dict[str, str]:
            return {"body": cp_bodies[self._turn_number]}

    class _Conn:
        def execute(self, _sql: str, params: tuple[str, int]) -> _Result:
            return _Result(int(params[1]))

    class _Connect:
        def __enter__(self) -> _Conn:
            return _Conn()

        def __exit__(self, *_args: object) -> None:
            return None

    with (
        patch(
            "agent_bus_store.tape_pour.list_checkpoint_turns",
            return_value=(cp1, cp2),
        ),
        patch("agent_bus_store.tape_pour.connect", return_value=_Connect()),
    ):
        cells = _cells_for_lane(
            thread_id="6341",
            lane_journals=journals,
            files_root=files_root,
        )

    closed = [c for c in cells if c["bus_turn_id"] is not None]
    open_cells = [c for c in cells if c["bus_turn_id"] is None]
    assert len(closed) == 2
    assert len(open_cells) == 1

    keys = {
        (c["cp_ordinal"], c["transcript_id"], c["turn_lo"], c["turn_hi"]): c["bus_turn_id"]
        for c in cells
    }
    assert keys[(1, _UUID, 0, 10)] == 5
    assert keys[(2, _UUID, 10, 15)] == 12
    assert keys[(3, _UUID, 15, 25)] is None


def test_b12_post_lid_turns_on_segment(tmp_path: Path) -> None:
    """B-12 / AC-5: sealed segment reports live JSONL tail as post_lid_turns."""
    files_root = tmp_path / "files"
    files_root.mkdir()
    sid = "cursor-2026-09-07-100000-pl1"
    rel_path = f"notes/system/transcripts/{sid}.md"
    path = files_root / rel_path
    path.parent.mkdir(parents=True)
    path.write_text(_verbatim_md(2, sid), encoding="utf-8")

    journal_row = {
        "id": 1,
        "session_id": sid,
        "conversation_uuid": _UUID,
        "prior_session_id": None,
        "file_path": rel_path,
        "entity_ids": json.dumps(["agent-bus:6341"]),
        "closed_by": "cursor",
        "dominant_lane": "6341",
    }

    with (
        patch("agent_bus_store.tape_membership.list_checkpoint_turns", return_value=()),
        patch("agent_bus_store.tape_pour.list_checkpoint_turns", return_value=()),
        patch("agent_bus_store.tape_render.post_lid_tail", return_value=(3, None)),
        patch("cortex_store.db.cortex_conn") as mock_conn,
        patch("cortex_store.events_tape.agent_bus_tape_rendered"),
        patch("cortex_store.events_tape.transcript_legacy_md_read"),
        patch("cortex_store.dispatch_ops._shared._FILES_ROOT", files_root),
    ):
        conn = mock_conn.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = [journal_row]
        result = render_tape(thread_id="6341")

    assert result["segments"][0]["post_lid_turns"] == 3


def test_b9_degrade_emits_index_lines_with_pointers() -> None:
    """B-9 / AC-6: budget overflow degrades dropped speech to Index lines."""
    cells = [
        {
            "transcript_id": _UUID,
            "turn_lo": 0,
            "turn_hi": 4,
            "bus_turn_id": 7,
        }
    ]
    messages = [
        {
            "role": "user",
            "content": "x" * 300,
            "session_id": "s1",
            "transcript_id": _UUID,
            "turn_index": i,
        }
        for i in range(1, 5)
    ]
    from agent_bus_store.tape_degrade import degrade_overflow_messages

    kept, index_rows, truncated, _degraded = degrade_overflow_messages(
        messages,
        cells=cells,
        budget_bytes=1200,
        thread_id="6341",
    )
    assert truncated is True
    assert len(json.dumps({"messages": kept, "index": index_rows}).encode("utf-8")) <= 1200
    assert index_rows
    assert index_rows[0]["transcript_span"] == "transcript:s1#turn-1"
    assert index_rows[0]["bus_turn_id"] == 7
    assert all(m.get("role") != "index" for m in kept)


def test_t14_byte_accurate_degrade_under_budget() -> None:
    messages = [
        {
            "role": "user",
            "content": "x" * 5000,
            "session_id": "s1",
            "transcript_id": _UUID,
            "turn_index": i,
        }
        for i in range(1, 101)
    ]
    from agent_bus_store.tape_degrade import degrade_overflow_messages

    kept, index_rows, truncated, _degraded = degrade_overflow_messages(
        messages,
        cells=[],
        budget_bytes=65536,
        thread_id="6341",
    )
    assert truncated is True
    assert len(json.dumps({"messages": kept, "index": index_rows}).encode("utf-8")) <= 65536
    assert kept
    assert all(m.get("role") != "index" for m in kept)


def test_t15_open_line_is_first_key() -> None:
    with (
        patch("agent_bus_store.tape_membership.list_checkpoint_turns", return_value=()),
        patch("agent_bus_store.tape_pour.list_checkpoint_turns", return_value=()),
        patch("cortex_store.db.cortex_conn") as mock_conn,
        patch("cortex_store.events_tape.agent_bus_tape_rendered"),
    ):
        conn = mock_conn.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = []
        result = render_tape(thread_id="6341")
    assert list(result.keys())[0] == "open_line"
    assert "summary" in result


def test_t16_mismatch_when_cp_anchor_beyond_sealed(tmp_path: Path) -> None:
    from agent_bus_store.tape_render import _build_mismatch_rows

    segments = [
        {
            "transcript_id": _UUID,
            "turn_hi": 5,
            "post_lid_turns": 0,
        }
    ]
    cells = [
        {
            "transcript_id": _UUID,
            "turn_hi": 10,
            "bus_turn_id": 3,
            "turn_lo": 0,
        }
    ]
    mismatch = _build_mismatch_rows(cells=cells, segments=segments)
    assert len(mismatch) == 1
    assert mismatch[0]["turns_at_cp"] == 10
    assert mismatch[0]["sealed_turn_hi"] == 5

