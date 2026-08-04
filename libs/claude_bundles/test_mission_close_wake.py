"""Unit tests for mission-close wake-path fail-closed gate."""

from __future__ import annotations

import pytest

from claude_bundles.mission_close_wake import (
    BEYOND_HEADING,
    BEYOND_NOTIFY_PREFIX,
    is_mission_closeout,
    refusal_envelope,
    validate_mission_close_wake,
    validate_mission_debrief_notify,
)

pytestmark = pytest.mark.offline

# Verbatim shape of agent-bus:6576 t67 — D10 named in flight, no wake path.
_T67_BODY = """\
TYPE: MISSION_CLOSEOUT
lane: agent-bus:6576

## AC status

4. Residuals commissioned + debrief notify — **met** (D10 in flight; awareness ping sent).
"""


def test_t67_shape_refused_missing_section() -> None:
    verdict = validate_mission_close_wake(
        subject="MISSION CLOSEOUT — 6576 orchestration fragility",
        body=_T67_BODY,
    )
    assert verdict.ok is False
    assert verdict.reason == "mission_close_wake_path_missing"
    assert BEYOND_HEADING in verdict.missed_tokens
    env = refusal_envelope(verdict)
    assert env["status"] == "blocked"
    assert "fix_hint" in env
    assert "missed_tokens" in env


def test_none_with_in_flight_refused() -> None:
    body = (
        "TYPE: MISSION_CLOSEOUT\n"
        "D10 commissioned, in flight.\n\n"
        f"{BEYOND_HEADING}\nnone\n"
    )
    verdict = validate_mission_close_wake(body=body)
    assert verdict.ok is False
    assert verdict.reason == "mission_close_uncollected_commission"


def test_wake_pathed_item_passes() -> None:
    body = (
        "TYPE: MISSION_CLOSEOUT\n"
        "D10 commissioned, in flight.\n\n"
        f"{BEYOND_HEADING}\n"
        "- D10 B-iii thin spec — collector: web-anthropic · "
        "followup: poll agent-bus:6576 after status:done\n"
    )
    verdict = validate_mission_close_wake(
        subject="MISSION CLOSEOUT — ok",
        body=body,
    )
    assert verdict.ok is True


def test_clean_none_passes() -> None:
    body = f"TYPE: MISSION_CLOSEOUT\n\n{BEYOND_HEADING}\nnone\n"
    assert validate_mission_close_wake(body=body).ok is True


def test_non_closeout_passes() -> None:
    assert validate_mission_close_wake(
        subject="DIRECTIVE D10",
        body="TYPE: DIRECTIVE\ncontract: implement\n",
    ).ok is True
    assert is_mission_closeout(subject="hello", body="TYPE: DIRECTIVE") is False


def test_item_without_wake_token_refused() -> None:
    body = (
        "TYPE: MISSION_CLOSEOUT\n\n"
        f"{BEYOND_HEADING}\n"
        "- D10 B-iii thin spec — commissioned, in flight\n"
    )
    verdict = validate_mission_close_wake(body=body)
    assert verdict.ok is False
    assert verdict.reason == "mission_close_wake_path_incomplete"
    assert len(verdict.missed_tokens) == 1
    assert "D10 B-iii thin spec" in verdict.missed_tokens[0]
    assert "collector:" not in verdict.missed_tokens[0]


def test_prose_only_section_refused() -> None:
    body = (
        "TYPE: MISSION_CLOSEOUT\n\n"
        f"{BEYOND_HEADING}\n"
        "D10 remains commissioned in flight; poll agent-bus:6576 after done.\n"
    )
    verdict = validate_mission_close_wake(body=body)
    assert verdict.ok is False
    assert verdict.reason == "mission_close_wake_path_incomplete"
    assert len(verdict.missed_tokens) == 1
    assert "D10 remains commissioned" in verdict.missed_tokens[0]


def test_debrief_notify_requires_beyond_line() -> None:
    body = (
        "The fleet used to lose track of wake debt after PARKED closes.\n\n"
        "Looking back: We treated wake as courtesy.\n\n"
        "Architecture: CSE Session Registry on cdp-registry + project_ask followup.\n\n"
        "Looking ahead: Enter /layer on the obligations todo.\n"
    )
    verdict = validate_mission_debrief_notify(
        subject="ULG mission debrief — 6576",
        body=body,
        tag="mission-debrief",
    )
    assert verdict.ok is False
    assert BEYOND_NOTIFY_PREFIX in verdict.missed_tokens


def test_debrief_notify_with_wake_passes() -> None:
    body = (
        "The fleet used to lose track of wake debt after PARKED closes.\n\n"
        "Looking back: We treated wake as courtesy.\n\n"
        "Architecture: CSE Session Registry on cdp-registry + project_ask followup.\n\n"
        "Looking ahead: Enter /layer on the obligations todo.\n\n"
        f"{BEYOND_NOTIFY_PREFIX} D10 — collector: this-seat · "
        "followup: poll 6576 after done"
    )
    verdict = validate_mission_debrief_notify(
        subject="ULG mission debrief",
        body=body,
        tag="mission-debrief",
    )
    assert verdict.ok is True


def test_debrief_notify_rejects_unnamed_systems() -> None:
    body = (
        "Something important about how the fleet knows what happened landed.\n\n"
        "Looking back: Prior shape was wrong.\n\n"
        "Architecture: A new projection holds session state somehow.\n\n"
        "Looking ahead: Keep going.\n\n"
        f"{BEYOND_NOTIFY_PREFIX} none"
    )
    verdict = validate_mission_debrief_notify(
        subject="ULG mission debrief",
        body=body,
        tag="mission-debrief",
    )
    assert verdict.ok is False
    assert verdict.reason == "mission_debrief_systems_unnamed"


# --- AC1 reproduction tests (correct behavior after bullet-item fix) ---

def test_ac1_prose_preamble_plus_token_bullets() -> None:
    """(i) Prose preamble + well-formed token-bearing bullets — passes after fix."""
    body = (
        "TYPE: MISSION_CLOSEOUT\n\n"
        f"{BEYOND_HEADING}\n"
        "The following items remain in flight after this close.\n"
        "- D10 B-iii thin spec — collector: web-anthropic · "
        "followup: poll agent-bus:6576 after status:done\n"
    )
    verdict = validate_mission_close_wake(body=body)
    assert verdict.ok is True


def test_ac1_hard_wrapped_bullet() -> None:
    """(ii) Single residual bullet hard-wrapped across two physical lines."""
    body = (
        "TYPE: MISSION_CLOSEOUT\n\n"
        f"{BEYOND_HEADING}\n"
        "- D10 B-iii thin spec — collector: web-anthropic · followup: poll\n"
        "agent-bus:6576 after status:done\n"
    )
    verdict = validate_mission_close_wake(body=body)
    assert verdict.ok is True


def test_ac1_mid_bullet_token_passes() -> None:
    """Token mid-bullet (not at line head) passes — retires a:27419 line-anchor cause."""
    body = (
        "TYPE: MISSION_CLOSEOUT\n\n"
        f"{BEYOND_HEADING}\n"
        "- D10 B-iii thin spec — commissioned, collector: web-anthropic · "
        "followup: poll agent-bus:6576 after status:done\n"
    )
    verdict = validate_mission_close_wake(body=body)
    assert verdict.ok is True


def test_land_residual_ide_collector_refused() -> None:
    body = (
        "TYPE: MISSION_CLOSEOUT\n\n"
        f"{BEYOND_HEADING}\n"
        "- CSR gapless unlanded on master — collector: cursor lead\n"
    )
    verdict = validate_mission_close_wake(body=body)
    assert verdict.ok is False
    assert verdict.reason == "mission_close_ide_collector_for_land"


def test_land_residual_cursor_auto_collector_ok() -> None:
    body = (
        "TYPE: MISSION_CLOSEOUT\n\n"
        f"{BEYOND_HEADING}\n"
        "- CSR gapless land on master — collector: cursor-auto\n"
    )
    verdict = validate_mission_close_wake(body=body)
    assert verdict.ok is True


def test_non_land_ide_collector_still_ok() -> None:
    body = (
        "TYPE: MISSION_CLOSEOUT\n\n"
        f"{BEYOND_HEADING}\n"
        "- D10 B-iii thin spec — collector: cursor lead · followup: poll 6576\n"
    )
    verdict = validate_mission_close_wake(body=body)
    assert verdict.ok is True
