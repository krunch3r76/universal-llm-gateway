"""Resume envelope anchors on hop checkpoints and narrow sealed pours."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from agent_bus_store.resume_envelope import build_resume_envelope

pytestmark = pytest.mark.offline


def test_build_resume_envelope_uses_last_session_tape() -> None:
    tape = {
        "messages": [{"role": "user", "content": "tail", "turn_index": 8}],
        "open_line": {
            "scope": "last_session",
            "last_cp": {
                "channel": "hop",
                "turn_hi": 9,
                "transcript_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            },
            "open_interval": {"transcript_ids": [], "turns": 0},
        },
        "truncated": False,
    }
    with patch(
        "agent_bus_store.resume_envelope.render_tape_with_harvest",
        return_value=tape,
    ):
        env = build_resume_envelope("10479")
    assert env["scope"] == "last_session"
    assert env["open_line"]["last_cp"]["channel"] == "hop"
    assert env["tape_verbal"]
