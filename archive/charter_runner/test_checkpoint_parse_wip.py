"""WIP-none body forms — charter eligibility must accept FOL + authoring variants."""

from __future__ import annotations

import pytest

from scripts.model_manager.ui.controller.charter_runner.checkpoint_schema import (
    _wip_is_none,
    parse_checkpoint,
)

pytestmark = pytest.mark.offline


@pytest.mark.parametrize(
    "text",
    [
        "none",
        "None",
        "WIP: none",
        "wip: none",
        "WIP=none",
        "wip=none",
        "WIP = none",
        "in-flight: none",
        "in_flight=none",
        "_None this window._",
        "none (window closing after this CHECKPOINT)",
        "WIP=none (gloss)",
        "- none",
        "",
    ],
)
def test_wip_is_none_accepts_documented_forms(text: str) -> None:
    assert _wip_is_none(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "WIP=holder 5855",
        "active: G2 A",
        "nest_under child 0558c502",
        "WIP=none\nstill holding",
    ],
)
def test_wip_is_none_rejects_active_forms(text: str) -> None:
    assert _wip_is_none(text) is False


def test_parse_checkpoint_wip_equals_none_eligible_shape() -> None:
    """Regression: wave-1 dogfood body used FOL ``WIP=none`` under ## WIP."""
    body = """# CHECKPOINT wave 1

## WIP / In-flight
WIP=none

## Next pickup
1. G2 — A + Gate-2 densify

## Steps
1. [x] G1
2. [ ] G2

## Frictions
_None this window._

— RESUME (any seat, no command): load agent-bus-discipline → this CHECKPOINT.
"""
    parsed = parse_checkpoint(body)
    assert parsed.wip_is_none is True
    assert parsed.next_pickup_gated is True
    assert parsed.wip_text == "WIP=none"
