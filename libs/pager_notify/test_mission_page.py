"""Unit tests for vision-shaped mission pager bodies."""

from __future__ import annotations

from pager_notify.mission_page import (
    extract_awareness_slots,
    format_mission_awareness_page,
    format_summons_stop_page,
    named_ulg_systems,
)


def test_mission_page_orders_vision_back_arch_ahead_beyond() -> None:
    _subject, body, tag = format_mission_awareness_page(
        subject="ULG test",
        vision="Vision sentence about knowing.",
        looking_back="We thought X; it was Y.",
        architecture="Finish line lives in the commission.",
        looking_ahead="Re-arm against the three folds.",
        beyond_bullets=["GIW restart — operator_gate: IDE"],
        tag="mission-debrief",
    )
    assert tag == "mission-debrief"
    assert body.index("Vision sentence") < body.index("Looking back:")
    assert body.index("Looking back:") < body.index("Architecture:")
    assert body.index("Architecture:") < body.index("Looking ahead:")
    assert body.index("Looking ahead:") < body.index("Beyond this close:")
    assert "operator_gate: IDE" in body


def test_summons_stop_arc_complete_is_mission_debrief_tag() -> None:
    subject, body, tag = format_summons_stop_page(
        reason="arc_complete",
        mission="sdk-align + branches + flows",
        looking_back="Sub-arc closed early.",
        architecture="Commission defines done.",
        looking_ahead="Check three folds.",
        beyond_bullets=["continue mission — followup: MONITOR"],
    )
    assert tag == "mission-debrief"
    assert "stopped — arc_complete" in subject
    assert "sdk-align + branches + flows" in body
    assert "Looking ahead:" in body


def test_extract_awareness_slots_from_atx() -> None:
    body = (
        "so_what: ULG grows CSE Session Registry\n\n"
        "## Vision\n"
        "Wake debt used to vanish after PARKED.\n\n"
        "## Architecture\n"
        "cdp-registry reducer + project_ask followup + agent-bus ledger.\n"
    )
    slots = extract_awareness_slots(body)
    assert "vanish" in slots.vision
    assert "cdp-registry" in slots.architecture
    assert "CSE Session Registry" in slots.so_what
    assert "project_ask" in named_ulg_systems(slots.architecture)
