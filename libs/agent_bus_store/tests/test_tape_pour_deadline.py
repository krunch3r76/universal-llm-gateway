"""Pour stops between segments when the resume deadline has passed."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from agent_bus_store.tape_pour import pour_lane_messages

pytestmark = pytest.mark.offline


def test_pour_stops_before_segment_when_deadline_passed() -> None:
    with patch("agent_bus_store.tape_pour._cells_for_lane", return_value=[]):
        (
            messages,
            _index,
            _cells,
            truncated,
            _payload,
            _tools,
            degraded,
            *_rest,
        ) = pour_lane_messages(
            thread_id="t",
            journals=[],
            segments=[{"session_id": "never-read"}],
            lane_journals=[],
            files_root=Path("."),
            scope="full",
            budget_bytes=1000,
            tools="none",
            include_extras=False,
            deadline=0.0,
        )
    assert messages == []
    assert truncated is True
    assert degraded == {"reason": "deadline"}
