"""Pour path: cell-scoped segment reads and last_session narrowing."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from agent_bus_store.tape_cells import _narrow_last_session_pour_cells
from agent_bus_store.tape_pour import pour_lane_messages

pytestmark = pytest.mark.offline

_TID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def test_pour_reads_only_cell_referenced_segments() -> None:
    """Segments outside selected cells must not hit disk read_text."""
    lane_journals = [
        {
            "session_id": "sid-a",
            "conversation_uuid": _TID,
            "verbatim_codec": "md-v1",
            "file_path": "notes/system/transcripts/sid-a.md",
        },
        {
            "session_id": "sid-b",
            "conversation_uuid": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            "verbatim_codec": "md-v1",
            "file_path": "notes/system/transcripts/sid-b.md",
        },
    ]
    segments = [
        {
            "session_id": "sid-a",
            "transcript_id": _TID,
            "turn_lo": 0,
            "turn_hi": 3,
            "turn_count": 3,
            "verbatim_codec": "md-v1",
            "from_sealed": True,
        },
        {
            "session_id": "sid-b",
            "transcript_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            "turn_lo": 0,
            "turn_hi": 10,
            "turn_count": 10,
            "verbatim_codec": "md-v1",
            "from_sealed": True,
        },
    ]
    cells = [
        {
            "cp_ordinal": 1,
            "transcript_id": _TID,
            "turn_lo": 0,
            "turn_hi": 3,
            "bus_turn_id": 10,
            "channel": "hop",
        }
    ]
    files_root = MagicMock()
    read_mock = MagicMock(return_value="# Session\n")
    files_root.__truediv__ = MagicMock(return_value=MagicMock(read_text=read_mock))
    journals = [{**j, "entity_ids": ["agent-bus:10479"]} for j in lane_journals]

    with (
        patch(
            "agent_bus_store.tape_pour._cells_for_lane",
            return_value=cells,
        ),
        patch(
            "agent_bus_store.tape_pour._last_session_cells",
            return_value=(cells, 0, 0, 0),
        ),
        patch(
            "agent_bus_store.tape_pour.messages_from_sealed_row",
            return_value=([], "md-v1"),
        ),
    ):
        pour_lane_messages(
            thread_id="10479",
            journals=journals,
            segments=segments,
            lane_journals=lane_journals,
            files_root=files_root,
            scope="last_session",
            budget_bytes=512_000,
            tools="none",
            include_extras=False,
        )

    assert read_mock.call_count == 1


def test_narrow_pour_open_interval_only() -> None:
    cells = [
        {
            "cp_ordinal": 2,
            "transcript_id": _TID,
            "turn_lo": 5,
            "turn_hi": 7,
            "bus_turn_id": 20,
            "channel": "hop",
        },
        {
            "cp_ordinal": 3,
            "transcript_id": _TID,
            "turn_lo": 7,
            "turn_hi": 9,
            "bus_turn_id": None,
        },
    ]
    narrowed = _narrow_last_session_pour_cells(cells)
    assert len(narrowed) == 1
    assert narrowed[0]["bus_turn_id"] is None
    assert narrowed[0]["turn_lo"] == 7


def test_narrow_pour_minimal_tail_when_fully_sealed() -> None:
    cells = [
        {
            "cp_ordinal": 2,
            "transcript_id": _TID,
            "turn_lo": 0,
            "turn_hi": 9,
            "bus_turn_id": 20,
            "channel": "hop",
        },
    ]
    narrowed = _narrow_last_session_pour_cells(cells)
    assert len(narrowed) == 1
    assert narrowed[0]["turn_lo"] == 7
    assert narrowed[0]["turn_hi"] == 9
