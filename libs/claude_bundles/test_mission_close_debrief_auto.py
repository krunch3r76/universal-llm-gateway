"""Unit tests for auto mission-debrief pager composition and delivery."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_bundles.mission_close_debrief_auto import (
    check_closeout_growth_map_gaps,
    compose_mission_debrief_from_closeout,
    deliver_mission_debrief_auto,
)
from claude_bundles.mission_close_wake import (
    BEYOND_HEADING,
    BEYOND_NOTIFY_PREFIX,
    validate_mission_debrief_notify,
)

pytestmark = pytest.mark.offline

_RICH_BODY = (
    "TYPE: MISSION_CLOSEOUT\n"
    "lane: agent-bus:6642\n"
    "so_what: ULG grows CSE Session Registry so wake debt cannot vanish\n\n"
    "## Vision\n"
    "The fleet used to lose track of wake debt after PARKED — a retained Cowork "
    "tab could sit reachable while nobody paid chat_delivery.\n\n"
    "## Looking back\n"
    "We treated wake as courtesy; unpaid debt was unobservable.\n\n"
    "## Architecture\n"
    "CSE Session Registry on cdp-registry reducer; project_ask followup pays "
    "WAKE; agent-bus remains the commission ledger.\n\n"
    "## Looking ahead\n"
    "Enter /layer on the obligations-plane todo at G2.\n\n"
    f"{BEYOND_HEADING}\n"
    "- D10 B-iii thin spec — collector: web-anthropic · "
    "followup: poll agent-bus:6642 after status:done\n"
)

_HOLLOW_BODY = (
    "TYPE: MISSION_CLOSEOUT\n"
    "lane: agent-bus:6642\n\n"
    "## AC status\n"
    "Charter heal landed; fleet is healthy again.\n\n"
    f"{BEYOND_HEADING}\n"
    "- D10 B-iii thin spec — collector: web-anthropic · "
    "followup: poll agent-bus:6642 after status:done\n"
)

_NONE_BODY = (
    "TYPE: MISSION_CLOSEOUT\n"
    "so_what: ULG closes mission with cortex + agent-bus residuals cleared\n\n"
    "## Vision\n"
    "Nothing further to know — the mission's knowing loop is complete.\n\n"
    "## Architecture\n"
    "agent-bus close + cortex assertions hold the final state; no new substrate.\n\n"
    f"{BEYOND_HEADING}\nnone\n"
)

_MISSING_BEYOND_BODY = (
    "TYPE: MISSION_CLOSEOUT\n\n"
    "## Vision\n"
    "The fleet gap around unpaid wake debt is closed in doctrine only.\n\n"
    "## Architecture\n"
    "CSE Session Registry plan on cortex; project_ask still owes live payment.\n"
)


def test_compose_happy_path_growth_map() -> None:
    composed = compose_mission_debrief_from_closeout(
        subject="MISSION CLOSEOUT — 6642",
        body=_RICH_BODY,
        thread_id="6642",
    )
    assert composed["tag"] == "mission-debrief"
    assert "COME TO IDE" not in composed["subject"]
    assert "CSE Session Registry" in composed["subject"] or "wake debt" in composed[
        "subject"
    ].casefold()
    assert "Architecture:" in composed["body"]
    assert "project_ask" in composed["body"]
    assert "Looking back:" in composed["body"]
    assert BEYOND_NOTIFY_PREFIX in composed["body"]
    assert "collector: web-anthropic" in composed["body"]
    verdict = validate_mission_debrief_notify(
        subject=composed["subject"],
        body=composed["body"],
        tag=composed["tag"],
    )
    assert verdict.ok is True


def test_compose_hollow_closeout_fails_validation() -> None:
    gap = check_closeout_growth_map_gaps(_HOLLOW_BODY)
    assert gap is not None
    assert gap.ok is False
    assert gap.reason in {
        "mission_debrief_vision_missing",
        "mission_debrief_architecture_missing",
        "mission_debrief_closeout_slots_missing",
        "mission_debrief_systems_unnamed",
    }


def test_deliver_hollow_closeout_refuses_before_stub_validate() -> None:
    mock_record = MagicMock()
    with patch(
        "claude_bundles.mission_close_debrief_auto.validate_mission_debrief_notify",
        return_value=__import__(
            "claude_bundles.mission_close_wake",
            fromlist=["MissionCloseWakeVerdict"],
        ).MissionCloseWakeVerdict(ok=True),
    ):
        outcome = deliver_mission_debrief_auto(
            closeout_subject="MISSION CLOSEOUT",
            closeout_body=_HOLLOW_BODY,
            thread_id="6642",
            from_agent="web-anthropic",
            record_fn=mock_record,
        )
    assert outcome["status"] == "rejected"
    assert outcome["reason"] in {
        "mission_debrief_vision_missing",
        "mission_debrief_architecture_missing",
        "mission_debrief_closeout_slots_missing",
    }


# Verbatim closeout bodies from agent-bus:7065 turns 114 and 115 (specimens).
_BODY_7065_114 = """\
TYPE: MISSION_CLOSEOUT
arc: 6655 / cursor-sdk-feature-alignment-operator-lane

Closing because the proof exists, not because the hop reported success.

## Work beyond this close

- propagation rows — collector: cdp-operator-6655-day2b on 7065 · followup: poll 7065
- LIFE_PROJECT_ROOT enable — collector: cdp-operator-6655-day2b on 7065 · followup: commission

Beyond this close: seven residuals — collector: cdp-operator-6655-day2b on 7065 · followup: poll 7065
"""

_BODY_7065_115 = """\
TYPE: MISSION_CLOSEOUT
arc: 6655 / cursor-sdk-feature-alignment-operator-lane

Amends `7065#114`, which posted but whose closeout-triggered debrief returned `mission_debrief_architecture_missing`.

Architecture: fleet operating-state snapshot and its life MCP `fs` read path; continuity-hop path in git_integration_worker (deferred sibling fork); closeout assembly path in cursor-auto; agent-bus as the persistent operator lane.

## Work beyond this close

- propagation rows — collector: cdp-operator-6655-day2b on 7065 · followup: poll 7065
- LIFE_PROJECT_ROOT enable — collector: cdp-operator-6655-day2b on 7065 · followup: commission

Beyond this close: seven residuals — collector: cdp-operator-6655-day2b on 7065 · followup: poll 7065
"""


def test_specimen_7065_114_refuses_missing_architecture_from_closeout() -> None:
    gap = check_closeout_growth_map_gaps(_BODY_7065_114)
    assert gap is not None
    assert gap.ok is False
    assert gap.reason == "mission_debrief_architecture_missing"
    assert gap.missed_tokens == ("Architecture: <named ULG systems>",)
    outcome = deliver_mission_debrief_auto(
        closeout_subject="MISSION CLOSEOUT",
        closeout_body=_BODY_7065_114,
        thread_id="7065",
        from_agent="web-anthropic",
        record_fn=lambda *_a, **_k: None,
    )
    assert outcome == {
        "status": "rejected",
        "reason": "mission_debrief_architecture_missing",
        "missed_tokens": ["Architecture: <named ULG systems>"],
    }


def test_specimen_7065_115_passes_compose_and_validate() -> None:
    gap = check_closeout_growth_map_gaps(_BODY_7065_115)
    assert gap is None
    composed = compose_mission_debrief_from_closeout(
        subject="MISSION CLOSEOUT",
        body=_BODY_7065_115,
        thread_id="7065",
    )
    verdict = validate_mission_debrief_notify(
        subject=composed["subject"],
        body=composed["body"],
        tag=composed["tag"],
    )
    assert verdict.ok is True
    mock_pager = AsyncMock(return_value=True)
    with (
        patch("pager_notify.life_notify.pager_enabled", return_value=True),
        patch("pager_notify.life_notify.notify_pager", mock_pager),
    ):
        outcome = deliver_mission_debrief_auto(
            closeout_subject="MISSION CLOSEOUT",
            closeout_body=_BODY_7065_115,
            thread_id="7065",
            from_agent="web-anthropic",
            record_fn=lambda *_a, **_k: None,
        )
    assert outcome["status"] == "sent"



def test_compose_none_beyond_path() -> None:
    composed = compose_mission_debrief_from_closeout(
        subject="MISSION CLOSEOUT",
        body=_NONE_BODY,
        thread_id="6642",
    )
    assert f"{BEYOND_NOTIFY_PREFIX}" in composed["body"]
    assert "none" in composed["body"].casefold()
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


def test_deliver_rejected_when_hollow() -> None:
    mock_record = MagicMock()
    with patch("pager_notify.life_notify.pager_enabled", return_value=True):
        outcome = deliver_mission_debrief_auto(
            closeout_subject="MISSION CLOSEOUT",
            closeout_body=_HOLLOW_BODY,
            thread_id="6642",
            from_agent="web-anthropic",
            record_fn=mock_record,
        )
    assert outcome["status"] == "rejected"
    mock_record.assert_called_once()
    assert mock_record.call_args.args[0] == "mcp.agentbus.mission_debrief.failed"


def test_deliver_sent_on_rich_closeout() -> None:
    mock_pager = AsyncMock(return_value=True)
    mock_record = MagicMock()
    with (
        patch("pager_notify.life_notify.pager_enabled", return_value=True),
        patch("pager_notify.life_notify.notify_pager", mock_pager),
    ):
        outcome = deliver_mission_debrief_auto(
            closeout_subject="MISSION CLOSEOUT — 6642",
            closeout_body=_RICH_BODY,
            thread_id="6642",
            from_agent="web-anthropic",
            record_fn=mock_record,
        )
    assert outcome["status"] == "sent"
    assert outcome.get("stamped_at")
    assert outcome["ref"] == "agent-bus:6642"
    mock_pager.assert_awaited_once()
    assert mock_record.call_args.args[0] == "mcp.agentbus.mission_debrief.sent"
    # Subject/body passed to pager are growth-map shaped.
    args = mock_pager.await_args
    body = args.args[1] if len(args.args) > 1 else args.kwargs.get("body", "")
    assert "Architecture:" in body
    assert "cdp-registry" in body or "project_ask" in body


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
