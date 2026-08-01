"""Unit tests for auto mission-debrief pager composition and delivery."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_bundles.mission_close_debrief_auto import (
    compose_mission_debrief_from_closeout,
    deliver_mission_debrief_auto,
)
from claude_bundles.mission_close_wake import (
    BEYOND_HEADING,
    BEYOND_NOTIFY_PREFIX,
    validate_mission_debrief_notify,
)

pytestmark = pytest.mark.offline

_WAKE_BODY = (
    "TYPE: MISSION_CLOSEOUT\n"
    "lane: agent-bus:6642\n\n"
    "## AC status\n"
    "Charter heal landed; fleet is healthy again.\n\n"
    f"{BEYOND_HEADING}\n"
    "- D10 B-iii thin spec — collector: web-anthropic · "
    "followup: poll agent-bus:6642 after status:done\n"
)

_NONE_BODY = (
    "TYPE: MISSION_CLOSEOUT\n\n"
    "## Outcome\n"
    "All work completed; mission arc is fully closed.\n\n"
    f"{BEYOND_HEADING}\nnone\n"
)

_MISSING_BEYOND_BODY = (
    "TYPE: MISSION_CLOSEOUT\n\n"
    "## Outcome\n"
    "Closed without beyond section.\n"
)


def test_compose_happy_path_with_beyond_section() -> None:
    composed = compose_mission_debrief_from_closeout(
        subject="MISSION CLOSEOUT — 6642",
        body=_WAKE_BODY,
        thread_id="6642",
    )
    assert composed["tag"] == "mission-debrief"
    assert "COME TO IDE" not in composed["subject"]
    assert "Mission debrief" in composed["subject"]
    assert "bus:6642" in composed["subject"]
    assert BEYOND_NOTIFY_PREFIX in composed["body"]
    assert "collector: web-anthropic" in composed["body"]
    verdict = validate_mission_debrief_notify(
        subject=composed["subject"],
        body=composed["body"],
        tag=composed["tag"],
    )
    assert verdict.ok is True


def test_compose_none_beyond_path() -> None:
    composed = compose_mission_debrief_from_closeout(
        subject="MISSION CLOSEOUT",
        body=_NONE_BODY,
        thread_id="6642",
    )
    assert f"{BEYOND_NOTIFY_PREFIX} none" in composed["body"]
    verdict = validate_mission_debrief_notify(
        subject=composed["subject"],
        body=composed["body"],
        tag=composed["tag"],
    )
    assert verdict.ok is True


def test_compose_missing_beyond_fails_validation() -> None:
    composed = compose_mission_debrief_from_closeout(
        subject="MISSION CLOSEOUT",
        body=_MISSING_BEYOND_BODY,
        thread_id="6642",
    )
    verdict = validate_mission_debrief_notify(
        subject=composed["subject"],
        body=composed["body"],
        tag=composed["tag"],
    )
    assert verdict.ok is False
    assert verdict.reason == "mission_debrief_beyond_missing"


def test_deliver_rejected_when_beyond_missing() -> None:
    mock_record = MagicMock()
    outcome = deliver_mission_debrief_auto(
        closeout_subject="MISSION CLOSEOUT",
        closeout_body=_MISSING_BEYOND_BODY,
        thread_id="6642",
        from_agent="web-anthropic",
        record_fn=mock_record,
    )
    assert outcome["status"] == "rejected"
    assert outcome["reason"] == "mission_debrief_beyond_missing"
    mock_record.assert_called_once()
    assert mock_record.call_args.args[0] == "mcp.agentbus.mission_debrief.failed"


def test_deliver_sent_on_valid_closeout() -> None:
    mock_pager = AsyncMock(return_value=True)
    mock_record = MagicMock()
    with (
        patch("pager_notify.life_notify.pager_enabled", return_value=True),
        patch("pager_notify.life_notify.notify_pager", mock_pager),
    ):
        outcome = deliver_mission_debrief_auto(
            closeout_subject="MISSION CLOSEOUT — 6642",
            closeout_body=_WAKE_BODY,
            thread_id="6642",
            from_agent="web-anthropic",
            record_fn=mock_record,
        )
    assert outcome["status"] == "sent"
    assert outcome.get("stamped_at")
    assert outcome["ref"] == "agent-bus:6642"
    mock_pager.assert_awaited_once()
    assert mock_record.call_args.args[0] == "mcp.agentbus.mission_debrief.sent"


def test_deliver_disabled_reports_status() -> None:
    with patch("pager_notify.life_notify.pager_enabled", return_value=False):
        outcome = deliver_mission_debrief_auto(
            closeout_subject="MISSION CLOSEOUT",
            closeout_body=_NONE_BODY,
            thread_id="6642",
            from_agent="web-anthropic",
            record_fn=lambda *_a, **_k: None,
        )
    assert outcome["status"] == "disabled"
    assert outcome["reason"] == "PAGER_NOTIFY_ENABLED=0"
