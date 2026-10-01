"""Tests for arc-7119 annotate-only effect-claim injection (limb A + limb C)."""

from __future__ import annotations

import pytest
from agent_bus_store.disposition import (
    body_has_disposition_type,
    first_line_is_disposition_type,
)
from agent_bus_store.wait_status import is_disposition_one_correction
from claude_bundles.pickup_awaits import is_cease_to_act

pytestmark = pytest.mark.offline

# Corpus instance 1 — R6 / stops existing (architecture rev-2 quote).
_INSTANCE_1 = (
    "TYPE: DIRECTIVE\n"
    "scope: arc-7119\n"
    "R6: the hop consumes zero free slots, so a full gate can never block "
    "succession — the deadlock does not need managing; it stops existing.\n"
)

# Corpus instance 2 — R7 / frees (positive control — demanded number).
_INSTANCE_2 = (
    "TYPE: DIRECTIVE\n"
    "scope: arc-7119\n"
    "R7 stops the accumulation and frees no slots tonight when two rows lack "
    "recorded predecessors after a correct R7 hop.\n"
)

# Corpus instance 3 — R8 on ungated LEG DISPOSITION / NO ACTION REQUESTED.
_INSTANCE_3 = (
    "TYPE: LEG DISPOSITION (rev 2)\n"
    "NO ACTION REQUESTED. Pointers, not state. Nothing of mine is in flight; "
    "verify before trusting that.\n\n"
    "Next fire, and it is not blocked on anything — R8, reserved headroom for "
    "the advisor rung. It needs no death signal, and it unblocks two things at "
    "once: the `/layer` G1 fire on `todo:cdp-satellite-seat-death-signal` "
    "(halted at `free_slots=0`) and the owed `cdp/fable` ratification.\n"
)

# Corpus instance 4 — rehearsal framing (DISPOSITION+DIRECTIVE body + vision).
_INSTANCE_4_BODY = (
    "TYPE: DISPOSITION+DIRECTIVE\n"
    "contract: investigate\n"
    "scope: readoption\n"
    "If re-adoption is possible, then a satellite restart stops being destructive, "
    "and every item in the cycle above unblocks at once — including the death signal, "
    "which appears to want the same probe.\n"
    "vision: If that restart can be made safe, the whole knot is one change wide.\n"
)








def test_instance_3_lexicon_would_match_if_scanned() -> None:
    """Document that lexical patterns exist but eligibility gate suppresses."""
    assert "not blocked on" in _INSTANCE_3.lower()
    assert "R8" in _INSTANCE_3










def test_leg_disposition_matches_disposition_family() -> None:
    first = "TYPE: LEG DISPOSITION (rev 2)"
    assert first_line_is_disposition_type(first) is True
    assert body_has_disposition_type(
        "TYPE: LEG DISPOSITION (rev 2)\nNO ACTION REQUESTED.\n"
    )
    assert (
        is_cease_to_act(body="TYPE: LEG DISPOSITION (rev 2)\nverdict: yield\n") is True
    )


def test_disposition_plus_directive_matches_disposition_family() -> None:
    assert first_line_is_disposition_type("TYPE: DISPOSITION+DIRECTIVE") is True


def test_plain_directive_not_disposition_type() -> None:
    assert first_line_is_disposition_type("TYPE: DIRECTIVE") is False


def test_is_disposition_one_correction_accepts_leg_disposition() -> None:
    turn = {
        "body": (
            "TYPE: LEG DISPOSITION (rev 2)\nverdict: one correction\nnotes follow\n"
        )
    }
    assert is_disposition_one_correction(turn) is True


