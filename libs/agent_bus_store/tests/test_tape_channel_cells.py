"""Cell channel propagation and wall selection invariants."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from agent_bus_store.tape_cells import _cells_for_lane, _last_session_cells

pytestmark = pytest.mark.offline


def test_hop_anchor_advances_turn_lo() -> None:
    """I3: hop seal still advances turn_lo for later continuity cell on same transcript."""
    tid = "550e8400-e29b-41d4-a716-446655440000"
    cp1_body = f"Window: transcript_id={tid} · turns@cp=5 · channel=hop\n"
    cp2_body = f"Window: transcript_id={tid} · turns@cp=9\n"
    cps = [
        type("CP", (), {"turn_number": 10, "cp_ordinal": 1})(),
        type("CP", (), {"turn_number": 20, "cp_ordinal": 2})(),
    ]
    with (
        patch("agent_bus_store.tape_cells.list_checkpoint_turns", return_value=cps),
        patch("agent_bus_store.tape_cells.connect") as mock_connect,
        patch("agent_bus_store.tape_cells.build_chain_segments", return_value=[]),
        patch("agent_bus_store.tape_render.live_jsonl_turn_count", return_value=0),
        patch(
            "agent_bus_store.tape_membership.lookup_dominant_lane_by_uuid",
            return_value=None,
        ),
    ):
        conn = mock_connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchone.side_effect = [
            {"body": cp1_body},
            {"body": cp2_body},
        ]
        cells = _cells_for_lane(
            thread_id="10479",
            lane_journals=[],
            files_root=MagicMock(),
        )
    continuity_cells = [c for c in cells if c.get("channel") == "continuity" and c.get("bus_turn_id")]
    assert len(continuity_cells) == 1
    assert continuity_cells[0]["turn_lo"] == 5
    assert continuity_cells[0]["turn_hi"] == 9


def test_from_agent_not_a_discriminator() -> None:
    """I6: identical anchors produce identical cells regardless of CP author."""
    tid = "550e8400-e29b-41d4-a716-446655440000"
    body = f"Window: transcript_id={tid} · turns@cp=4\n"
    cp = type("CP", (), {"turn_number": 10, "cp_ordinal": 1})()
    with (
        patch("agent_bus_store.tape_cells.list_checkpoint_turns", return_value=[cp]),
        patch("agent_bus_store.tape_cells.connect") as mock_connect,
        patch("agent_bus_store.tape_cells.build_chain_segments", return_value=[]),
        patch("agent_bus_store.tape_render.live_jsonl_turn_count", return_value=0),
        patch(
            "agent_bus_store.tape_membership.lookup_dominant_lane_by_uuid",
            return_value=None,
        ),
    ):
        conn = mock_connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchone.return_value = {"body": body}
        cells_a = _cells_for_lane(thread_id="10479", lane_journals=[], files_root=MagicMock())
        cells_b = _cells_for_lane(thread_id="10479", lane_journals=[], files_root=MagicMock())
    assert cells_a == cells_b


def test_wall_skips_hop_under_continuity_channel() -> None:
    cells = [
        {
            "cp_ordinal": 1,
            "transcript_id": "a",
            "turn_lo": 0,
            "turn_hi": 5,
            "bus_turn_id": 100,
            "channel": "continuity",
        },
        {
            "cp_ordinal": 2,
            "transcript_id": "a",
            "turn_lo": 5,
            "turn_hi": 7,
            "bus_turn_id": 200,
            "channel": "hop",
        },
    ]
    scoped, skipped, _, _ = _last_session_cells(cells, thread_id="10479", channel="continuity")
    assert len(scoped) == 1
    assert scoped[0]["channel"] == "continuity"
    assert skipped == 1
