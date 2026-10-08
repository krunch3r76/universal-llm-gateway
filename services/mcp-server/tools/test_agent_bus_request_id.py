"""agent_bus request entry refuses before request_id intake (a:38728)."""

from __future__ import annotations

from unittest.mock import patch

from tools.agent_bus.request import _request_dispatch


def test_request_id_path_refuses_before_send() -> None:
    with (
        patch("tools.agent_bus.request._send_dispatch") as send_mock,
        patch("tools.agent_bus.request.record") as record_mock,
    ):
        result = _request_dispatch(
            new_slug="rid-echo",
            to="cursor",
            subject="probe",
            body="brief",
            from_agent="web-anthropic",
            contract="answer",
            request_id="rid-6328-a",
        )
    assert result == {
        "error": (
            "cursor-auto is retired (a:38728). Code commission: team_dispatch on ulg-code. "
            "Life CSE: life_dispatch. A bus turn: agent_bus send."
        ),
        "reason": "cursor_auto_retired",
    }
    send_mock.assert_not_called()
    record_mock.assert_called_once_with(
        "mcp.agentbus.request.rejected",
        reason="cursor_auto_retired",
    )
