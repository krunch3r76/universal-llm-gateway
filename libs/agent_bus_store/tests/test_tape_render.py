"""Tests for tape_render CP-cell partition."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from agent_bus_store.tape_render import render_tape

pytestmark = pytest.mark.offline


@patch("agent_bus_store.tape_render.list_checkpoint_turns")
@patch("cortex_store.db.cortex_conn")
def test_render_tape_empty_lane(mock_conn, mock_cps) -> None:
    mock_cps.return_value = ()
    conn = mock_conn.return_value.__enter__.return_value
    conn.execute.return_value.fetchall.return_value = []
    result = render_tape(thread_id="100")
    assert result["thread_id"] == "100"
    assert result["segment_count"] == 0
    assert result["truncated"] is False
