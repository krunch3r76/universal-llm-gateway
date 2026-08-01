"""MCP agent_bus send/reply attach mission_debrief_notify on MISSION_CLOSEOUT."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import agent_bus as agent_bus_module  # noqa: E402

_CLOSEOUT_BODY = (
    "TYPE: MISSION_CLOSEOUT\n\n"
    "## Work beyond this close\n"
    "none\n"
)


def test_send_impl_attaches_mission_debrief_notify() -> None:
    relay_result = {
        "send_path": "continue",
        "thread": {"id": "6642"},
        "turn": {"turn_number": 59},
    }
    debrief_outcome = {"status": "sent", "stamped_at": "2026-08-01T00:00:00+00:00"}

    with patch.object(agent_bus_module, "_relay", return_value=relay_result):
        with patch.object(agent_bus_module, "record", lambda *_a, **_k: None):
            with patch(
                "claude_bundles.mission_close_debrief_auto.deliver_mission_debrief_auto",
                return_value=debrief_outcome,
            ) as mock_deliver:
                result = agent_bus_module._send_impl(
                    new_slug=None,
                    thread="6642",
                    to="all",
                    subject="MISSION CLOSEOUT — 6642",
                    body=_CLOSEOUT_BODY,
                    from_agent="web-anthropic",
                    summary=None,
                    tags=None,
                    lifecycle_state=None,
                    after_turn=0,
                    status="open",
                    mark_read=False,
                    close=False,
                    attachments=None,
                    allow_long_body=False,
                )

    assert "error" not in result
    assert result["mission_debrief_notify"] == debrief_outcome
    mock_deliver.assert_called_once_with(
        closeout_subject="MISSION CLOSEOUT — 6642",
        closeout_body=_CLOSEOUT_BODY,
        thread_id="6642",
        from_agent="web-anthropic",
    )


def test_reply_impl_attaches_mission_debrief_notify() -> None:
    relay_result = {"turn_number": 59, "id": 991}
    debrief_outcome = {"status": "sent", "stamped_at": "2026-08-01T00:00:00+00:00"}

    with patch.object(agent_bus_module, "_relay", return_value=relay_result):
        with patch.object(agent_bus_module, "record", lambda *_a, **_k: None):
            with patch(
                "claude_bundles.mission_close_debrief_auto.deliver_mission_debrief_auto",
                return_value=debrief_outcome,
            ) as mock_deliver:
                result = agent_bus_module._reply_impl(
                    thread="6642",
                    to="all",
                    subject="MISSION CLOSEOUT — 6642",
                    body=_CLOSEOUT_BODY,
                    after_turn=58,
                    from_agent="web-anthropic",
                    status="open",
                    mark_read=False,
                    close=False,
                )

    assert "error" not in result
    assert result["mission_debrief_notify"] == debrief_outcome
    mock_deliver.assert_called_once()


def test_send_impl_skips_debrief_for_non_closeout() -> None:
    relay_result = {
        "send_path": "continue",
        "thread": {"id": "6642"},
        "turn": {"turn_number": 2},
    }

    with patch.object(agent_bus_module, "_relay", return_value=relay_result):
        with patch.object(agent_bus_module, "record", lambda *_a, **_k: None):
            with patch(
                "claude_bundles.mission_close_debrief_auto.deliver_mission_debrief_auto",
            ) as mock_deliver:
                result = agent_bus_module._send_impl(
                    new_slug=None,
                    thread="6642",
                    to="cursor",
                    subject="DIRECTIVE D10",
                    body="TYPE: DIRECTIVE\ncontract: implement\n",
                    from_agent="web-anthropic",
                    summary=None,
                    tags=None,
                    lifecycle_state=None,
                    after_turn=0,
                    status="open",
                    mark_read=False,
                    close=False,
                    attachments=None,
                    allow_long_body=False,
                )

    assert "error" not in result
    assert "mission_debrief_notify" not in result
    mock_deliver.assert_not_called()
