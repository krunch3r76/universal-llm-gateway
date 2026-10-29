"""Orion wake pipeline handoff carries dispatcher claim metadata."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bus_watch.endpoint_wake_dispatcher import _orion_post

pytestmark = pytest.mark.offline


def test_orion_post_includes_wake_id_and_covered_turns() -> None:
    rows = [
        {
            "thread_id": "21207",
            "turn_id": 10,
            "turn_number": 3,
            "agent": "orion",
            "from_agent": "maestro",
        },
        {
            "thread_id": "21207",
            "turn_id": 11,
            "turn_number": 4,
            "agent": "orion",
            "from_agent": "maestro",
        },
    ]
    sub = {
        "surface": "chatgpt",
        "title": "https://chatgpt.com/c/6ae2e1bc-0c94-83e8-9f21-62467c4935d5",
        "agent_id": "6ae2e1bc-0c94-83e8-9f21-62467c4935d5",
        "id": 42,
    }
    captured: dict = {}

    def _fake_post(url: str, *, json: dict, headers: dict) -> MagicMock:
        captured["options"] = json["pipeline_options"]
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [{"message": {"content": '{"ok": true, "delivered": true}'}}],
        }
        return resp

    client = MagicMock()
    client.post.side_effect = _fake_post
    client.__enter__ = lambda self: self
    client.__exit__ = lambda *a: None

    with patch(
        "bus_watch.endpoint_wake_dispatcher.make_sync_client", return_value=client
    ):
        state, reason = _orion_post(
            rows,
            sub,
            "3,4",
            from_agent="maestro",
            wake_id="wake-abc",
        )
    assert state == "delivered"
    opts = captured["options"]
    assert opts["wake_id"] == "wake-abc"
    assert opts["turn_ids"] == [10, 11]
    assert opts["covered_turn_ids"] == [10, 11]
    assert opts["binding_reference"] == "42"
