"""Tests for scope=window cell selection."""

from __future__ import annotations

import pytest

from agent_bus_store.tape_pour import _filter_messages_to_cells, _window_cells

pytestmark = pytest.mark.offline

_TID = "d556c84f-524e-4e57-b38c-6fdc3eafe8fb"
_OTHER = "04aab53e-82f5-44d3-b09e-9dfb247d67d7"


def _cell(
    cp_ordinal: int,
    transcript_id: str,
    turn_lo: int,
    turn_hi: int,
    bus_turn_id: int | None,
) -> dict:
    return {
        "cp_ordinal": cp_ordinal,
        "transcript_id": transcript_id,
        "turn_lo": turn_lo,
        "turn_hi": turn_hi,
        "boundary": None,
        "bus_turn_id": bus_turn_id,
    }


def test_window_cells_includes_prior_and_open() -> None:
    cells = [
        _cell(111, _OTHER, 0, 100, 507),
        _cell(112, _TID, 0, 120, 515),
        _cell(113, _TID, 120, 520, 520),
        _cell(114, _TID, 520, 525, None),
    ]
    selected = _window_cells(cells, transcript_id=_TID, prior_cells=1)
    ordinals = [c["cp_ordinal"] for c in selected]
    assert ordinals == [111, 112, 113, 114]
    assert selected[-1]["bus_turn_id"] is None


def test_window_cells_prior_cells_zero() -> None:
    cells = [
        _cell(111, _OTHER, 0, 100, 507),
        _cell(112, _TID, 0, 120, 515),
    ]
    selected = _window_cells(cells, transcript_id=_TID, prior_cells=0)
    assert [c["cp_ordinal"] for c in selected] == [112]


def test_window_cells_empty_when_transcript_missing() -> None:
    cells = [_cell(111, _OTHER, 0, 100, 507)]
    assert _window_cells(cells, transcript_id=_TID, prior_cells=1) == []


def test_filter_messages_to_cells_respects_turn_bounds() -> None:
    cells = [_cell(112, _TID, 100, 120, 515)]
    messages = [
        {"transcript_id": _TID, "turn_index": 99, "role": "user", "content": "x"},
        {"transcript_id": _TID, "turn_index": 110, "role": "user", "content": "y"},
        {"transcript_id": _OTHER, "turn_index": 110, "role": "user", "content": "z"},
    ]
    filtered = _filter_messages_to_cells(messages, cells)
    assert len(filtered) == 1
    assert filtered[0]["turn_index"] == 110
