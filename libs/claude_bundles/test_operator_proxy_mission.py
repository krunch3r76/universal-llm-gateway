"""Offline tests for operator-proxy mission prompt ensure."""

from __future__ import annotations

import pytest

from claude_bundles.act_receipt import parse_act_receipt
from claude_bundles.operator_proxy_mission import (
    _FORBIDDEN_HEADING,
    LIFE_SURFACE_FORBIDDEN_TOOLS,
    LIFE_SURFACE_LEGAL_TOOLS,
    ensure_operator_proxy_mission_prompt,
    is_operator_proxy_mission_purpose,
    purpose_implies_mission,
)

pytestmark = pytest.mark.offline


def test_purpose_recognition() -> None:
    assert is_operator_proxy_mission_purpose("operator-proxy")
    assert is_operator_proxy_mission_purpose("mission")
    assert is_operator_proxy_mission_purpose("OPERATOR_PROXY")
    assert not is_operator_proxy_mission_purpose("ask")
    assert purpose_implies_mission("ask", "purpose: mission\n# Body")
    assert not purpose_implies_mission("ask", "# Sealed R-admit")


def test_ensure_injects_chips_and_briefing() -> None:
    out = ensure_operator_proxy_mission_prompt("# Mission\nDo the thing.\n")
    assert out.startswith("/cdp-operator-proxy\n/reasoning-posture\n")
    assert "/completion-provenance-discipline\n" in out
    assert "Status / rank / liveness register (BINDING — member 6)" in out
    assert "## Mission seat map (BINDING" in out
    assert "cursor-auto-tick-work-posting.md" in out
    assert "# Mission\nDo the thing." in out
    assert "## Life surface act path (BINDING)" in out
    assert "## ACT-RECEIPT (BINDING" in out


def test_ensure_injects_self_scheduled_wake_guide() -> None:
    """First-dispatch briefing carries wake guide operating shape + carry-items."""
    out = ensure_operator_proxy_mission_prompt("# Mission\n")
    assert "cdp-seat-wake-heartbeat.md" in out
    assert "Self-scheduled wake" in out
    assert "first dispatch" in out
    assert "persistent: true" in out
    assert "1800000ms" in out
    assert "Monitor timeout — RESOLVED" in out
    assert "NOT the user" in out
    assert "does not prevent stopping" in out or "does not prevent the stop" in out
    assert "send_later" in out
    assert "TaskStop" in out
    assert "Audit guardrails" in out
    assert "not refuted" in out
    assert "Before fleet codification" in out


def test_ensure_idempotent_when_chips_present() -> None:
    once = ensure_operator_proxy_mission_prompt("TYPE: DIRECTIVE\nintent: birth\n")
    twice = ensure_operator_proxy_mission_prompt(once)
    assert twice.count("/cdp-operator-proxy") == 1
    assert twice.count("## Mission seat map (BINDING") == 1


def test_ensure_adds_missing_chip_only() -> None:
    out = ensure_operator_proxy_mission_prompt(
        "/cdp-operator-proxy\n\n# Already chipped\n"
    )
    assert out.startswith("/cdp-operator-proxy\n/reasoning-posture\n")
    assert "## Mission seat map (BINDING" in out


def test_structural_briefing_a7_escalate_is_agent_bus_not_team_dispatch() -> None:
    """F7/A7: escalate verb is agent_bus; team_dispatch only under FORBIDDEN heading."""
    out = ensure_operator_proxy_mission_prompt("# Mission\n")
    seat_section = out.split("## Life surface act path")[0]
    assert "agent_bus" in seat_section.lower()
    assert "team_dispatch" not in seat_section
    forbidden_idx = out.index(_FORBIDDEN_HEADING)
    seat_idx = out.index("## Mission seat map")
    team_dispatch_after_forbidden = "team_dispatch" in out[forbidden_idx:]
    assert team_dispatch_after_forbidden
    assert out.index("team_dispatch") > seat_idx


def test_legal_subset_forbidden_disjoint_a9() -> None:
    assert LIFE_SURFACE_LEGAL_TOOLS.isdisjoint(LIFE_SURFACE_FORBIDDEN_TOOLS)
    for tool in LIFE_SURFACE_LEGAL_TOOLS:
        assert f"`{tool}`" in ensure_operator_proxy_mission_prompt("# x\n")


def test_operator_proxy_mission_seat_map_names_reachable_independent_check() -> None:
    out = ensure_operator_proxy_mission_prompt("# Mission\n")
    seat_section = out.split("## Life surface act path")[0]
    assert "cdp/fable" in seat_section
    assert "cursor/grok-4.5" in seat_section
    assert "model_pin_refused" in seat_section
    assert "prefer `cursor/gpt-5.6-terra` when bindable" not in seat_section


def test_briefing_receipt_example_parses_d3() -> None:
    out = ensure_operator_proxy_mission_prompt("# Mission\n")
    marker = "```act-receipt"
    start = out.index(marker)
    end = out.index("```", start + len(marker))
    fence = out[start : end + 3]
    assert parse_act_receipt(fence) is not None
