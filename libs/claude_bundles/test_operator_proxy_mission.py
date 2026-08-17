"""Offline tests for operator-proxy mission prompt ensure."""

from __future__ import annotations

import pytest

from claude_bundles.act_receipt import parse_act_receipt
from claude_bundles.operator_proxy_mission import (
    _FORBIDDEN_HEADING,
    LIFE_SURFACE_FORBIDDEN_TOOLS,
    LIFE_SURFACE_LEGAL_TOOLS,
    MISSION_SKILL_SLUGS,
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
    """First-dispatch briefing suspends keep-alive; sole-wake / one-off CDP OK."""
    out = ensure_operator_proxy_mission_prompt("# Mission\n")
    assert "cdp-seat-wake-heartbeat.md" in out
    assert "Self-scheduled wake" in out or "keep-alive" in out.lower()
    assert "SUSPENDED" in out
    assert "Do not arm Monitor" in out
    assert "send_later" in out
    assert "TaskStop" in out
    assert "Sole-wake" in out or "sole-wake" in out.lower() or "PRIMARY" in out
    # Historical arm recipes must not ship as first-dispatch defaults.
    # §8 may *name* the forbidden tokens as a warning; they must not be arm instructions.
    assert "Do not arm Monitor" in out
    assert "does NOT make Monitor unbounded" in out
    assert "while true; do sleep 240" not in out
    assert "Consume-time wake affinity" in out
    assert "Absence is not permission" in out
    assert "missing (file absent): default STAND_DOWN" in out


def test_ensure_idempotent_when_chips_present() -> None:
    once = ensure_operator_proxy_mission_prompt("TYPE: DIRECTIVE\nintent: birth\n")
    twice = ensure_operator_proxy_mission_prompt(once)
    assert twice.count("/cdp-operator-proxy") == 1
    assert twice.count("## Mission seat map (BINDING") == 1
    assert twice.count("/reasoning-posture") == 1
    # Counted in the leading chip block only — the slug also appears in the
    # seat-map prose below it.
    assert twice.split("\n\n", 1)[0].count("/completion-provenance-discipline") == 1
    # strip() on re-entry may drop a trailing newline; slash block must stay single.
    assert twice.rstrip("\n") == once.rstrip("\n")


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
    assert "cursor/grok-4.6" in seat_section
    assert "model_pin_refused" in seat_section
    assert "prefer `cursor/gpt-5.6-terra` when bindable" not in seat_section


def test_briefing_one_operator_cse_per_lane() -> None:
    out = ensure_operator_proxy_mission_prompt("# Mission\n")
    assert "One operator CSE per lane" in out
    assert "predecessors, not peers" in out
    assert "Never touch operator CSEs on other lanes" in out


def test_briefing_receipt_example_parses_d3() -> None:
    out = ensure_operator_proxy_mission_prompt("# Mission\n")
    marker = "```act-receipt"
    start = out.index(marker)
    end = out.index("```", start + len(marker))
    fence = out[start : end + 3]
    assert parse_act_receipt(fence) is not None


def test_propagate_contract_documents_allow_self_preempt() -> None:
    """M3: operator briefing surfaces allow_self_preempt (skill already did)."""
    from claude_bundles.operator_proxy_tier_m import tier_m_authoring_block

    block = tier_m_authoring_block()
    assert "allow_self_preempt" in block
    assert "defaults **True**" in block or "defaults **True**".replace(
        "*", ""
    ) in block.replace("*", "")
    assert "force: false" in block.lower() or "``force: false``" in block
    assert (
        "not** the auto-escalation veto" in block
        or "not the auto-escalation veto" in block
    )
    # Shorthand + structured example surfaces both name the knob.
    assert block.count("allow_self_preempt") >= 2


def test_skill_surface_introspects_instead_of_asserting_loaded() -> None:
    """Chips are a request; seat must introspect and self-fetch gaps."""
    out = ensure_operator_proxy_mission_prompt("# Mission\n")
    for slug in MISSION_SKILL_SLUGS:
        assert f"`{slug}`" in out
    assert "Use the `" in out
    assert "`<slug>` skill" in out
    assert "complete set attachable" not in out
    for slug in (
        "operator-proxy-substrate",
        "claude-ai-cdp-navigation",
        "path-sim",
    ):
        assert f"`{slug}`" in out
    assert "decision:operator-proxy-skill-surface-split" in out
    assert "request**, not" in out or "request, not a receipt" in out.lower()
