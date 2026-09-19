"""Unit tests for last_session cell scoping (_last_session_cells)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from agent_bus_store.tape_cells import _last_session_cells
from agent_bus_store.tape_pour import pour_lane_messages

pytestmark = pytest.mark.offline

_ROOT = "10479"
_UUID_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_UUID_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _sealed(
    *,
    cp_ordinal: int,
    transcript_id: str = "a",
    turn_lo: int = 0,
    turn_hi: int = 5,
    bus_turn_id: int = 10,
    dominant_lane: str | None = None,
    ownership_source: str = "lane_journal",
) -> dict:
    cell = {
        "cp_ordinal": cp_ordinal,
        "transcript_id": transcript_id,
        "turn_lo": turn_lo,
        "turn_hi": turn_hi,
        "bus_turn_id": bus_turn_id,
    }
    if dominant_lane is not None:
        cell["dominant_lane"] = dominant_lane
        cell["ownership_source"] = ownership_source
    return cell


def _open(
    *,
    cp_ordinal: int,
    transcript_id: str = "a",
    turn_lo: int = 5,
    turn_hi: int = 12,
    dominant_lane: str | None = None,
    ownership_source: str = "lane_journal",
) -> dict:
    cell = {
        "cp_ordinal": cp_ordinal,
        "transcript_id": transcript_id,
        "turn_lo": turn_lo,
        "turn_hi": turn_hi,
        "bus_turn_id": None,
    }
    if dominant_lane is not None:
        cell["dominant_lane"] = dominant_lane
        cell["ownership_source"] = ownership_source
    return cell


def test_wall_plus_all_open_cells() -> None:
    cells = [_sealed(cp_ordinal=5, bus_turn_id=100), _sealed(cp_ordinal=6, bus_turn_id=200), _open(cp_ordinal=99)]
    scoped, skipped, foreign, unresolved = _last_session_cells(cells, thread_id=_ROOT)
    assert [c["cp_ordinal"] for c in scoped] == [6, 99]
    assert skipped == 0
    assert foreign == 0
    assert unresolved == 0


def test_no_checkpoint_returns_all_cells() -> None:
    cells = [_open(cp_ordinal=1), _open(cp_ordinal=2, transcript_id="b")]
    scoped, skipped, foreign, unresolved = _last_session_cells(cells, thread_id=_ROOT)
    assert scoped == cells
    assert skipped == 0
    assert foreign == 0
    assert unresolved == 0


def test_wall_ordinal_includes_all_cells_at_wall_not_only_last() -> None:
    cells = [
        _sealed(cp_ordinal=4, bus_turn_id=40, turn_lo=0, turn_hi=3),
        _sealed(cp_ordinal=5, bus_turn_id=50, turn_lo=3, turn_hi=6),
        _sealed(cp_ordinal=5, bus_turn_id=51, turn_lo=6, turn_hi=9, transcript_id="b"),
        _open(cp_ordinal=7),
        _open(cp_ordinal=100, transcript_id="c"),
    ]
    scoped, skipped, foreign, unresolved = _last_session_cells(cells, thread_id=_ROOT)
    assert [c["cp_ordinal"] for c in scoped] == [5, 5, 7, 100]
    assert skipped == 0
    assert foreign == 0
    assert unresolved == 0


def test_excludes_sealed_cells_before_wall() -> None:
    cells = [
        _sealed(cp_ordinal=1, bus_turn_id=10),
        _sealed(cp_ordinal=2, bus_turn_id=20),
        _open(cp_ordinal=50),
    ]
    scoped, skipped, foreign, unresolved = _last_session_cells(cells, thread_id=_ROOT)
    assert [c["cp_ordinal"] for c in scoped] == [2, 50]
    assert skipped == 0
    assert foreign == 0
    assert unresolved == 0


def _channel_cell(
    *,
    cp_ordinal: int,
    channel: str = "continuity",
    bus_turn_id: int | None = 10,
    transcript_id: str = "a",
    turn_lo: int = 0,
    turn_hi: int = 5,
) -> dict:
    return {
        "cp_ordinal": cp_ordinal,
        "transcript_id": transcript_id,
        "turn_lo": turn_lo,
        "turn_hi": turn_hi,
        "bus_turn_id": bus_turn_id,
        "channel": channel,
    }


def test_wall_skips_hop_cells() -> None:
    cells = [
        _channel_cell(cp_ordinal=1, channel="continuity", bus_turn_id=100, turn_hi=5),
        _channel_cell(cp_ordinal=2, channel="hop", bus_turn_id=200, turn_lo=5, turn_hi=7),
        _open(cp_ordinal=3),
    ]
    scoped, skipped, foreign, unresolved = _last_session_cells(
        cells, thread_id=_ROOT, channel="continuity"
    )
    assert [c["cp_ordinal"] for c in scoped] == [1, 3]
    assert skipped == 1
    assert foreign == 0
    assert unresolved == 0


def test_channel_all_matches_pre_change_wall() -> None:
    cells = [
        _channel_cell(cp_ordinal=1, channel="continuity", bus_turn_id=100),
        _channel_cell(cp_ordinal=2, channel="hop", bus_turn_id=200, turn_lo=5, turn_hi=7),
        _open(cp_ordinal=3),
    ]
    scoped_all, skipped_all, _, _ = _last_session_cells(cells, thread_id=_ROOT, channel="all")
    assert [c["cp_ordinal"] for c in scoped_all] == [2, 3]
    assert skipped_all == 0


def test_hop_cells_skipped_zero_under_channel_all() -> None:
    cells = [_channel_cell(cp_ordinal=1, channel="hop", bus_turn_id=100), _open(cp_ordinal=2)]
    _, skipped, _, _ = _last_session_cells(cells, thread_id=_ROOT, channel="all")
    assert skipped == 0


def test_hop_cells_skipped_zero_without_hop_anchors() -> None:
    cells = [_channel_cell(cp_ordinal=1), _open(cp_ordinal=2)]
    _, skipped, _, _ = _last_session_cells(cells, thread_id=_ROOT, channel="continuity")
    assert skipped == 0


def test_drops_foreign_dominant_lane_open_cell() -> None:
    cells = [
        _sealed(cp_ordinal=1, bus_turn_id=10, transcript_id=_UUID_A, dominant_lane=_ROOT),
        _open(
            cp_ordinal=2,
            transcript_id=_UUID_B,
            turn_lo=21,
            turn_hi=28,
            dominant_lane="10526",
            ownership_source="uuid_lookup",
        ),
        _open(
            cp_ordinal=3,
            transcript_id=_UUID_A,
            turn_lo=2,
            turn_hi=5,
            dominant_lane=_ROOT,
        ),
    ]
    scoped, _, foreign, unresolved = _last_session_cells(cells, thread_id=_ROOT)
    assert foreign == 1
    assert unresolved == 0
    assert all(c["transcript_id"] != _UUID_B for c in scoped)


def _verbatim_md(turn_count: int, *, filler: str = "x") -> str:
    lines = ["# Session test\n"]
    for i in range(1, turn_count + 1):
        lines.extend(
            [
                f"## Turn {i}",
                "### User",
                filler * 200,
                "### Assistant",
                filler * 200,
                "",
            ]
        )
    lines.append("\n## Session Summary\n\n**Decisions:** test\n")
    return "\n".join(lines)


def _counsel_fixture_messages() -> list[dict]:
    """Synthetic messages: foreign B tail + root-owned A open tail."""
    msgs: list[dict] = []
    for turn in range(22, 29):
        msgs.append(
            {
                "role": "user",
                "content": f"foreign counsel turn {turn}",
                "transcript_id": _UUID_B,
                "session_id": "sid-b",
                "turn_index": turn,
            }
        )
    for turn in range(3, 6):
        msgs.append(
            {
                "role": "user",
                "content": f"root-owned turn {turn}",
                "transcript_id": _UUID_A,
                "session_id": "sid-a",
                "turn_index": turn,
            }
        )
    return msgs


@patch("agent_bus_store.tape_render.anchor_jsonl_messages")
@patch("agent_bus_store.tape_cells.list_checkpoint_turns")
@patch("agent_bus_store.tape_cells.connect")
@patch("agent_bus_store.tape_cells.build_chain_segments")
@patch("agent_bus_store.tape_render.live_jsonl_turn_count")
@patch("agent_bus_store.tape_membership.lookup_dominant_lane_by_uuid")
@pytest.mark.parametrize("budget_bytes", [24_000, 512_000])
def test_counsel_on_root_cp_fixture(
    mock_uuid_lookup,
    mock_live_count,
    mock_chain,
    mock_connect,
    mock_cps,
    mock_anchor,
    budget_bytes: int,
) -> None:
    """AC-2d: foreign counsel open cell dropped; root-owned speech survives."""
    mock_uuid_lookup.side_effect = lambda uuid: "10526" if uuid == _UUID_B else _ROOT
    mock_live_count.side_effect = lambda tid: 28 if tid == _UUID_B else 5
    mock_chain.return_value = [
        {"transcript_id": _UUID_A, "turn_lo": 0, "turn_hi": 2, "session_id": "sid-a"},
        {"transcript_id": _UUID_B, "turn_lo": 0, "turn_hi": 21, "session_id": "sid-b"},
    ]
    cp_j_body = f"Window: transcript_id={_UUID_B} · turns@cp=21\n"
    cp_k_body = f"Window: transcript_id={_UUID_A} · turns@cp=2\n"
    cps = [
        type("CP", (), {"turn_number": 10, "cp_ordinal": 1})(),
        type("CP", (), {"turn_number": 20, "cp_ordinal": 2})(),
    ]
    mock_cps.return_value = cps
    conn = mock_connect.return_value.__enter__.return_value
    conn.execute.return_value.fetchone.side_effect = [
        {"body": cp_j_body},
        {"body": cp_k_body},
    ]
    mock_anchor.return_value = _counsel_fixture_messages()

    lane_journals = [
        {
            "session_id": "sid-a",
            "conversation_uuid": _UUID_A,
            "_dominant_lane": _ROOT,
            "verbatim_codec": "md-v1",
            "file_path": "notes/system/transcripts/sid-a.md",
        },
        {
            "session_id": "sid-b",
            "conversation_uuid": _UUID_B,
            "_dominant_lane": "10526",
            "verbatim_codec": "md-v1",
            "file_path": "notes/system/transcripts/sid-b.md",
        },
    ]
    files_root = MagicMock()
    journals = [{**j, "entity_ids": [f"agent-bus:{_ROOT}"]} for j in lane_journals]
    segments = [
        {
            "session_id": "sid-a",
            "transcript_id": _UUID_A,
            "turn_lo": 0,
            "turn_hi": 2,
            "turn_count": 2,
            "verbatim_codec": "md-v1",
            "from_sealed": True,
        },
        {
            "session_id": "sid-b",
            "transcript_id": _UUID_B,
            "turn_lo": 0,
            "turn_hi": 21,
            "turn_count": 21,
            "verbatim_codec": "md-v1",
            "from_sealed": True,
        },
    ]

    with patch("continuity_tape.seal_reader.messages_from_sealed_row", return_value=([], "md-v1")):
        (
            messages,
            _index,
            cells,
            _truncated,
            _payload,
            _tools,
            _degraded,
            _hop,
            foreign_excluded,
            ownership_unresolved,
            _codec_fallback,
        ) = pour_lane_messages(
            thread_id=_ROOT,
            journals=journals,
            segments=segments,
            lane_journals=lane_journals,
            files_root=files_root,
            scope="last_session",
            budget_bytes=budget_bytes,
            tools="none",
            include_extras=budget_bytes >= 512_000,
        )

    assert foreign_excluded >= 1
    assert ownership_unresolved == 0
    contents = [str(m.get("content") or "") for m in messages]
    assert not any("foreign counsel" in c for c in contents)
    assert any("root-owned" in c for c in contents)
    if budget_bytes >= 512_000:
        assert all(m.get("transcript_id") != _UUID_B for m in messages)
    else:
        assert foreign_excluded == 1
