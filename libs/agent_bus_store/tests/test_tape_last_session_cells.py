"""Unit tests for last_session cell scoping (_last_session_cells)."""

from __future__ import annotations

import pytest
from agent_bus_store.tape_cells import _last_session_cells

pytestmark = pytest.mark.offline


def _sealed(
    *,
    cp_ordinal: int,
    transcript_id: str = "a",
    turn_lo: int = 0,
    turn_hi: int = 5,
    bus_turn_id: int = 10,
) -> dict:
    return {
        "cp_ordinal": cp_ordinal,
        "transcript_id": transcript_id,
        "turn_lo": turn_lo,
        "turn_hi": turn_hi,
        "bus_turn_id": bus_turn_id,
    }


def _open(
    *,
    cp_ordinal: int,
    transcript_id: str = "a",
    turn_lo: int = 5,
    turn_hi: int = 12,
) -> dict:
    return {
        "cp_ordinal": cp_ordinal,
        "transcript_id": transcript_id,
        "turn_lo": turn_lo,
        "turn_hi": turn_hi,
        "bus_turn_id": None,
    }


def test_wall_plus_all_open_cells() -> None:
    cells = [_sealed(cp_ordinal=5, bus_turn_id=100), _sealed(cp_ordinal=6, bus_turn_id=200), _open(cp_ordinal=99)]
    scoped, skipped = _last_session_cells(cells)
    assert [c["cp_ordinal"] for c in scoped] == [6, 99]
    assert skipped == 0


def test_no_checkpoint_returns_all_cells() -> None:
    cells = [_open(cp_ordinal=1), _open(cp_ordinal=2, transcript_id="b")]
    scoped, skipped = _last_session_cells(cells)
    assert scoped == cells
    assert skipped == 0


def test_wall_ordinal_includes_all_cells_at_wall_not_only_last() -> None:
    cells = [
        _sealed(cp_ordinal=4, bus_turn_id=40, turn_lo=0, turn_hi=3),
        _sealed(cp_ordinal=5, bus_turn_id=50, turn_lo=3, turn_hi=6),
        _sealed(cp_ordinal=5, bus_turn_id=51, turn_lo=6, turn_hi=9, transcript_id="b"),
        _open(cp_ordinal=7),
        _open(cp_ordinal=100, transcript_id="c"),
    ]
    scoped, skipped = _last_session_cells(cells)
    assert [c["cp_ordinal"] for c in scoped] == [5, 5, 7, 100]
    assert skipped == 0


def test_excludes_sealed_cells_before_wall() -> None:
    cells = [
        _sealed(cp_ordinal=1, bus_turn_id=10),
        _sealed(cp_ordinal=2, bus_turn_id=20),
        _open(cp_ordinal=50),
    ]
    scoped, skipped = _last_session_cells(cells)
    assert [c["cp_ordinal"] for c in scoped] == [2, 50]
    assert skipped == 0


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
    scoped, skipped = _last_session_cells(cells, channel="continuity")
    assert [c["cp_ordinal"] for c in scoped] == [1, 3]
    assert skipped == 1


def test_channel_all_matches_pre_change_wall() -> None:
    cells = [
        _channel_cell(cp_ordinal=1, channel="continuity", bus_turn_id=100),
        _channel_cell(cp_ordinal=2, channel="hop", bus_turn_id=200, turn_lo=5, turn_hi=7),
        _open(cp_ordinal=3),
    ]
    scoped_all, skipped_all = _last_session_cells(cells, channel="all")
    assert [c["cp_ordinal"] for c in scoped_all] == [2, 3]
    assert skipped_all == 0


def test_hop_cells_skipped_zero_under_channel_all() -> None:
    cells = [_channel_cell(cp_ordinal=1, channel="hop", bus_turn_id=100), _open(cp_ordinal=2)]
    _, skipped = _last_session_cells(cells, channel="all")
    assert skipped == 0


def test_hop_cells_skipped_zero_without_hop_anchors() -> None:
    cells = [_channel_cell(cp_ordinal=1), _open(cp_ordinal=2)]
    _, skipped = _last_session_cells(cells, channel="continuity")
    assert skipped == 0
