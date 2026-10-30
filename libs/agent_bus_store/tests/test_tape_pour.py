"""Pour path: cell-scoped segment reads and last_session narrowing."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from agent_bus_store.tape_catalog_elide import (
    elide_ide_catalog_blocks,
    excise_turn_one_resume_bootstrap,
)
from agent_bus_store.tape_cells import _narrow_last_session_pour_cells
from agent_bus_store.tape_pour import pour_lane_messages

pytestmark = pytest.mark.offline

_TID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def _liaison_bootstrap_user() -> str:
    return (
        "resume 10479\n\n"
        "Liaison IDE hop (attended register) tip_cp=1. LOAD skill.\n"
        "OPERATOR LOOP: close with a situation report to the operator. "
        "A finished watch with no next leg is not a situation report."
    )


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
    assert len(narrowed) == 2
    open_only = [c for c in narrowed if c.get("bus_turn_id") is None]
    assert len(open_only) == 1
    assert open_only[0]["turn_lo"] == 7


_UUID_OLD = "11111111-1111-1111-1111-111111111111"
_UUID_NEW = "22222222-2222-2222-2222-222222222222"


def test_narrow_pour_ignores_superseded_historical_open_cells() -> None:
    cells = [
        {
            "cp_ordinal": 5,
            "transcript_id": _UUID_OLD,
            "turn_lo": 0,
            "turn_hi": 18,
            "bus_turn_id": 10,
        },
        {
            "cp_ordinal": 55,
            "transcript_id": _UUID_NEW,
            "turn_lo": 0,
            "turn_hi": 2,
            "bus_turn_id": 20,
        },
        {
            "cp_ordinal": 56,
            "transcript_id": _UUID_OLD,
            "turn_lo": 18,
            "turn_hi": 21,
            "bus_turn_id": None,
        },
        {
            "cp_ordinal": 56,
            "transcript_id": _UUID_NEW,
            "turn_lo": 2,
            "turn_hi": 5,
            "bus_turn_id": None,
        },
    ]
    narrowed = _narrow_last_session_pour_cells(cells)
    open_tids = {
        str(c["transcript_id"])
        for c in narrowed
        if c.get("bus_turn_id") is None
    }
    assert _UUID_OLD not in open_tids
    assert _UUID_NEW in open_tids
    sealed = [c for c in narrowed if c.get("bus_turn_id") is not None]
    assert len(sealed) == 1
    assert sealed[0]["cp_ordinal"] == 55


def test_last_session_drops_turn_one_bootstrap_when_turn_two_present() -> None:
    messages = [
        {
            "role": "user",
            "content": _liaison_bootstrap_user(),
            "transcript_id": _TID,
            "turn_index": 1,
        },
        {
            "role": "assistant",
            "content": "Where we left off: prior state. Rules I'm holding: K1.",
            "transcript_id": _TID,
            "turn_index": 1,
        },
        {
            "role": "user",
            "content": "<user_query>post-resume work</user_query>",
            "transcript_id": _TID,
            "turn_index": 2,
        },
        {
            "role": "assistant",
            "content": "done",
            "transcript_id": _TID,
            "turn_index": 2,
        },
    ]
    out = excise_turn_one_resume_bootstrap(messages)
    assert all(int(m.get("turn_index") or 0) != 1 for m in out)
    assert any("post-resume work" in str(m.get("content") or "") for m in out)


def test_one_turn_hop_keeps_elided_turn_one_bootstrap() -> None:
    messages = [
        {
            "role": "user",
            "content": _liaison_bootstrap_user(),
            "transcript_id": _TID,
            "turn_index": 1,
        },
        {
            "role": "assistant",
            "content": "Where we left off: only turn.",
            "transcript_id": _TID,
            "turn_index": 1,
        },
    ]
    excised = excise_turn_one_resume_bootstrap(messages)
    assert len(excised) == 2
    elided = elide_ide_catalog_blocks(excised)
    user = elided[0]["content"]
    assert "[elided liaison_hop_bootstrap:" in user
    assert "Liaison IDE hop" not in user


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
