"""Tests for arc-7119 annotate-only effect-claim injection (limb A + limb C)."""

from __future__ import annotations

import pytest

from agent_bus_store.disposition import (
    body_has_disposition_type,
    first_line_is_disposition_type,
)
from agent_bus_store.wait_status import is_disposition_one_correction
from claude_bundles.pickup_awaits import is_cease_to_act
from services.git_integration_worker.cursor_auto.directive import build_sdk_message
from services.git_integration_worker.cursor_auto.effect_claim import (
    extract_limb_a_claims,
    extract_limb_c_claims,
    effect_claim_injection_lines,
    is_effect_claim_scan_eligible,
)

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


def test_instance_1_trips_limb_a() -> None:
    claims = extract_limb_a_claims(_INSTANCE_1)
    assert claims
    assert any("stops existing" in c for c in claims)


def test_instance_2_trips_limb_a() -> None:
    claims = extract_limb_a_claims(_INSTANCE_2)
    assert claims
    assert any("frees" in c for c in claims)


def test_instance_3_expected_miss_not_eligible() -> None:
    """Instance 3 — ungated NO ACTION REQUESTED surface; silence is coverage."""
    assert is_effect_claim_scan_eligible(_INSTANCE_3) is False
    assert extract_limb_a_claims(_INSTANCE_3) == ()
    assert extract_limb_c_claims(_INSTANCE_3) == ()
    assert effect_claim_injection_lines(_INSTANCE_3) == []


def test_instance_3_lexicon_would_match_if_scanned() -> None:
    """Document that lexical patterns exist but eligibility gate suppresses."""
    assert "not blocked on" in _INSTANCE_3.lower()
    assert "R8" in _INSTANCE_3


def test_instance_4_trips_limb_a_and_limb_c() -> None:
    limb_a = extract_limb_a_claims(_INSTANCE_4_BODY)
    limb_c = extract_limb_c_claims(_INSTANCE_4_BODY)
    assert limb_c
    assert any("unblocks at once" in c.lower() for c in limb_c)
    assert any("one change wide" in c for c in limb_a) or limb_c


def test_build_sdk_message_injects_effect_claim_block_for_instance_4() -> None:
    message = build_sdk_message(_INSTANCE_4_BODY, contract="investigate")
    assert "## Effect claim verification (annotate-only — arc 7119)" in message
    assert "effect_index:" in message
    assert "current_state | future_transitions | both" in message
    assert "annotate-only" in message
    assert "does not block" in message


def test_build_sdk_message_skips_instance_3() -> None:
    message = build_sdk_message(_INSTANCE_3, contract="implement")
    assert "## Effect claim verification" not in message


def test_injection_is_annotate_only_no_blocking_hooks() -> None:
    """AC5 — injection adds prompt text only; no admit/relay gate symbols."""
    lines = effect_claim_injection_lines(_INSTANCE_1)
    joined = "\n".join(lines).lower()
    assert "annotate-only" in joined
    assert "does not block" in joined
    assert "refuse" in joined or "fail the turn" in joined
    # Module exports no blocking API surface.
    import services.git_integration_worker.cursor_auto.effect_claim as mod

    public = set(mod.__all__)
    assert not any(name.startswith("block") for name in public)
    assert not any(name.startswith("refuse") for name in public)


def test_leg_disposition_matches_disposition_family() -> None:
    first = "TYPE: LEG DISPOSITION (rev 2)"
    assert first_line_is_disposition_type(first) is True
    assert body_has_disposition_type(f"TYPE: LEG DISPOSITION (rev 2)\nNO ACTION REQUESTED.\n")
    assert is_cease_to_act(body="TYPE: LEG DISPOSITION (rev 2)\nverdict: yield\n") is True


def test_disposition_plus_directive_matches_disposition_family() -> None:
    assert first_line_is_disposition_type("TYPE: DISPOSITION+DIRECTIVE") is True


def test_plain_directive_not_disposition_type() -> None:
    assert first_line_is_disposition_type("TYPE: DIRECTIVE") is False


def test_is_disposition_one_correction_accepts_leg_disposition() -> None:
    turn = {
        "body": (
            "TYPE: LEG DISPOSITION (rev 2)\n"
            "verdict: one correction\n"
            "notes follow\n"
        )
    }
    assert is_disposition_one_correction(turn) is True


def test_effect_index_grammar_forced_ternary_in_prompt() -> None:
    lines = effect_claim_injection_lines(_INSTANCE_1)
    text = "\n".join(lines)
    assert "effect_claim:" in text
    assert "effect_metric:" in text
    assert "effect_basis:" in text
    assert "effect_index:" in text
    assert "no deferral" in text.lower()
