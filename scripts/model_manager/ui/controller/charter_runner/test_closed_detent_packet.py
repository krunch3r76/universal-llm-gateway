"""Closed-detent packet selection from Next-pickup triage token."""

from __future__ import annotations

import pytest

from scripts.model_manager.ui.controller.charter_runner.checkpoint_schema import (
    parse_checkpoint,
    pickup_detent,
)
from scripts.model_manager.ui.controller.charter_runner.window_exec import (
    select_packet,
)

pytestmark = pytest.mark.offline

_CLOSED_BODY = """\
# CHECKPOINT

## WIP / In-flight
_None this window._

## Next pickup
1. G1 — `todo:friction-99` follow-on from root 5854 (spawned_by_friction=99) · detent=closed · fix parser

## Steps
1. [ ] G1 — closed follow-on

## Frictions
_None this window._

— RESUME (any seat, no command): load agent-bus-discipline → this CHECKPOINT.
"""

_FULL_BODY = """\
# CHECKPOINT

## WIP / In-flight
_None this window._

## Next pickup
1. G1 — Q / L0 for architecture redesign · detent=wide

## Steps
1. [ ] G1 — Q

## Frictions
_None this window._

— RESUME (any seat, no command): load agent-bus-discipline → this CHECKPOINT.
"""


def test_pickup_detent_closed() -> None:
    parsed = parse_checkpoint(_CLOSED_BODY)
    assert pickup_detent(parsed) == "closed"


def test_select_packet_closed_detent_subject() -> None:
    parsed = parse_checkpoint(_CLOSED_BODY)
    packet, subject = select_packet(
        "5854",
        parsed,
        scoreboard_uri=None,
        window_index=1,
        admission_mode="autonomous",
    )
    assert "closed-detent" in subject
    assert "CLOSED-DETENT" in packet
    assert "Do NOT fire G3 R-admit" in packet


def test_select_packet_wide_uses_full_arc() -> None:
    parsed = parse_checkpoint(_FULL_BODY)
    assert pickup_detent(parsed) == "wide"
    packet, subject = select_packet(
        "5854",
        parsed,
        scoreboard_uri=None,
        window_index=1,
        admission_mode="autonomous",
    )
    assert "background lead" in subject
    assert "G3  R-admit" in packet or "R-admit" in packet
    assert "CLOSED-DETENT" not in packet
