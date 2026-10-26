"""Tests for maestro operator-lane self-hop knob resolution."""

from __future__ import annotations

from claude_bundles.maestro_self_hop import (
    ELSEWHERE_HOP_MODEL,
    MAESTRO_LANE_ID,
    maestro_self_hop_refusal_tail,
    parse_k91_from_house_rules,
    resolve_maestro_self_hop,
)
from claude_bundles.operator_proxy_mission import ensure_operator_proxy_mission_prompt


def test_parse_k91_from_house_rules_row() -> None:
    text = (
        "- K91 (12286#4800): maestro_self_hop "
        "desired_model=cdp/fable-5.1 desired_effort=medium until further notice.\n"
    )
    knob = parse_k91_from_house_rules(text)
    assert knob is not None
    assert knob.desired_model == "cdp/fable-5.1"
    assert knob.desired_effort == "medium"


def test_resolve_maestro_lane_from_config_when_no_k91_row() -> None:
    knob = resolve_maestro_self_hop(
        house_rules_text="- K22 line\n", lane_id=MAESTRO_LANE_ID
    )
    assert knob.desired_model == "cdp/fable-5.1"
    assert knob.desired_effort == "medium"


def test_non_maestro_lane_uses_k22_without_fable() -> None:
    knob = resolve_maestro_self_hop(lane_id="99999")
    assert knob.desired_model == ELSEWHERE_HOP_MODEL
    assert knob.desired_effort == ""


def test_refusal_tail_exact_shape_on_maestro_lane() -> None:
    knob = resolve_maestro_self_hop(lane_id=MAESTRO_LANE_ID)
    assert maestro_self_hop_refusal_tail(knob) == (
        "maestro house: desired_model=cdp/fable-5.1 desired_effort=medium "
        "(K91, FACT 12286#4800); elsewhere cdp/opus-5.5-extra (K22)"
    )


def test_housekeeping_hop_step_non_maestro_lane_uses_opus() -> None:
    birth = "c" * 32
    caller = (
        "TYPE: CONTINUITY_HANDOFF\n"
        "thread_id: 99999\n"
        f"successor_birth_id: {birth}\n"
        "hop_kind: housekeeping\n"
    )
    sidecar = "## CURRENT LEG\n### OWED / NEXT\nfixture\n### HOUSE RULES\n- K1 rule\n"
    out = ensure_operator_proxy_mission_prompt(
        caller,
        standing_handoff_text=sidecar,
        roster_text="",
        fetch_turns=lambda _lane: [],
        journal_text="",
        register_text="",
    )
    step_six = next(
        line for line in out.splitlines() if line.startswith("6. agent_bus(hop")
    )
    assert "desired_model=cdp/opus-5.5-extra" in step_six
    assert "cdp/fable-5.1" not in step_six
    assert "desired_effort=" not in step_six
