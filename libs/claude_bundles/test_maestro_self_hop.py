"""Tests for maestro operator-lane self-hop knob resolution."""

from __future__ import annotations

from claude_bundles.maestro_self_hop import (
    maestro_self_hop_refusal_tail,
    parse_k91_from_house_rules,
    resolve_maestro_self_hop,
)


def test_parse_k91_from_house_rules_row() -> None:
    text = (
        "- K91 (12286#4800): maestro_self_hop "
        "desired_model=cdp/fable-5.1 desired_effort=medium until further notice.\n"
    )
    knob = parse_k91_from_house_rules(text)
    assert knob is not None
    assert knob.desired_model == "cdp/fable-5.1"
    assert knob.desired_effort == "medium"


def test_resolve_defaults_from_lane_config_when_no_k91_row() -> None:
    knob = resolve_maestro_self_hop(house_rules_text="- K22 line\n", lane_id="12286")
    assert knob.desired_model == "cdp/fable-5.1"
    assert knob.desired_effort == "medium"


def test_refusal_tail_exact_shape() -> None:
    knob = resolve_maestro_self_hop(lane_id="12286")
    assert maestro_self_hop_refusal_tail(knob) == (
        "maestro house: desired_model=cdp/fable-5.1 desired_effort=medium "
        "(K91, FACT 12286#4800); elsewhere cdp/opus-5.5-extra (K22)"
    )
