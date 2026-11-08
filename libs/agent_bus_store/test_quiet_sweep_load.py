"""quiet_sweep._load_lane includes read_at on LaneTurnView (friction a:43412)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from agent_bus_store.quiet_sweep import _load_lane


def test_load_lane_maps_read_at() -> None:
    """Breaks when SELECT omits read_at — harvest gate never sees mark_read."""
    turn_row = {
        "turn_number": 2,
        "from_agent": "cursor-sdk",
        "created_at": "2026-08-07T01:00:00Z",
        "subject": "done",
        "body": "status: complete",
        "read_at": "2026-08-07T01:00:01Z",
    }
    thread_row = {"bus_lifecycle_state": "active"}
    link_row = {
        "execution_id": "exec-1",
        "terminal_status": "completed",
        "terminal_at": "2026-08-07T01:00:00Z",
        "linked_at": "2026-08-07T00:50:00Z",
        "pipeline_id": "cursor-sdk-generate",
    }

    conn = MagicMock()
    conn.execute.side_effect = [
        MagicMock(fetchone=MagicMock(return_value=thread_row)),
        MagicMock(fetchall=MagicMock(return_value=[link_row])),
        MagicMock(fetchall=MagicMock(return_value=[turn_row])),
    ]
    cm = MagicMock()
    cm.__enter__.return_value = conn
    cm.__exit__.return_value = False

    with patch("agent_bus_store.quiet_sweep.connect", return_value=cm):
        loaded = _load_lane("T-load")

    assert loaded is not None
    _lifecycle, _links, turns = loaded
    assert len(turns) == 1
    assert turns[0].read_at == "2026-08-07T01:00:01Z"
